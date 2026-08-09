# -*- coding: utf-8 -*-
"""Локальный API-сервис img2img helper для Photoshop.

Сервис сохраняет существующий backend ComfyUI и добавляет независимый backend
Forge Neo с интерфейсами, описанными поставляемыми JSON-схемами.
"""

from __future__ import annotations

import atexit
import base64
import copy
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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


API_HOST = "127.0.0.1"
DEFAULT_COMFY_HOST = "127.0.0.1"
API_RECEIVE_PORT = 6370   # На этом порту Python принимает команды JSX.
API_REPLY_PORT = 6371     # На этот порт Python отправляет ответы JSX.
API_PROTOCOL = 1
VERSION = "0.151"

# Единый объект идентичности приложения. Пользовательские каталоги, имена
# служебных файлов и расположение поставляемых схем вычисляются только отсюда.
APP = {
    "name": "img2img helper",
    "slug": "img2img-helper",
    "python_module": "img2img-api",
    "data_folder": "img2img helper",
    "schema_folder": "forge-schemas",
    "lock_file": "img2img-api.lock",
    "runtime_file": "runtime.json",
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
FORGE_REFERENCE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


# Максимальный размер одного JSON-сообщения от JSX. Workflow целиком через
# API не передаётся, поэтому 32 МБ оставляют большой запас.
MAX_API_MESSAGE = 32 * 1024 * 1024

# Через сколько секунд бездействия фоновый процесс завершается самостоятельно.
IDLE_TIMEOUT_SECONDS = 15 * 60

# Как часто проверять историю ComfyUI во время генерации.
HISTORY_POLL_INTERVAL = 0.35

# Максимальный возраст временных каталогов перед автоматической очисткой.
TEMP_MAX_AGE_SECONDS = 24 * 60 * 60
UPLOAD_SUBFOLDER = APP["upload_subfolder"]
OUTPUT_SUBFOLDER = "Img2imgHelper"

# Версия формата внутреннего кеша. При изменении структуры увеличить число.
CACHE_VERSION = 1
# Версия сокращённой /object_info-схемы, которая хранится рядом с анализом.
# Старый cache без этого поля используется как analysis cache, но один раз
# дополняется настоящими типами из ComfyUI.
VALIDATION_SCHEMA_VERSION = 1
# UUID анализатора не является версией схемы. Новое значение принудительно
# сбрасывает только кеш анализа workflow после изменения правил распознавания.
ANALYZER_UUID = "7b8ac290-d69b-4b3e-aff4-69b238bfe71f"

# Упрощённые теги, которые пользователь может дописать к названию ноды прямо
# в интерфейсе ComfyUI.
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
LOCK_FILE = STATE_DIR / APP["lock_file"]
RUNTIME_FILE = STATE_DIR / APP["runtime_file"]
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
    return module


DEEP_TRANSLATOR_MODULE: Any = None
PIL_IMAGE_MODULE: Any = None
PIL_IMAGE_OPS_MODULE: Any = None


def prepare_required_modules() -> None:
    """Checks and installs all third-party modules required by the helper.

    Dependency preparation happens before the local API socket is opened, so a
    successfully started server is immediately ready for both prompt
    translation and Forge ImageStitch. Internet is only required when one of
    the packages is absent and pip must download it.
    """

    global DEEP_TRANSLATOR_MODULE, PIL_IMAGE_MODULE, PIL_IMAGE_OPS_MODULE

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

    if errors:
        raise UserVisibleError(
            "Could not prepare required Python modules:\n"
            + "\n".join(f"- {item}" for item in errors)
            + f"\n\nDetails: {LOG_FILE}"
        )

    LOGGER.info("Required Python modules are ready: deep-translator, Pillow")


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
    """ASCII-only JSON для старого Socket/eval стека ExtendScript.

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

    This runs before a new helper request is queued, so every file already
    present under ``output/Img2imgHelper`` belongs to an older request.
    """

    root = _existing_directory(output_folder)
    if not root:
        return
    base = (root / OUTPUT_SUBFOLDER).resolve()
    if not base.is_dir():
        return
    try:
        for child in base.rglob("*"):
            try:
                if child.is_file():
                    child.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Could not delete old ComfyUI output file: %s", child)
        for child in sorted((item for item in base.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
            try:
                child.rmdir()
            except OSError:
                pass
    except OSError:
        LOGGER.warning("Could not inspect ComfyUI helper output folder: %s", base)


def cleanup_comfy_request_outputs(output_folder: Optional[Path], request_id: str) -> None:
    root = _existing_directory(output_folder)
    if not root:
        return
    base = (root / OUTPUT_SUBFOLDER).resolve()
    if not base.is_dir():
        return
    prefix = safe_filename(request_id)
    if not prefix:
        return
    try:
        for target in base.rglob("*"):
            try:
                resolved = target.resolve()
                if not resolved.is_file() or (resolved.parent != base and base not in resolved.parents):
                    continue
                relative = resolved.relative_to(base)
                if not any(str(part).startswith(prefix) for part in relative.parts):
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


class UserVisibleError(RuntimeError):
    """Ожидаемая ошибка workflow/ComfyUI без технического traceback в UI."""


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
    ) -> Path:
        """Скачивает output в формате, который Photoshop сможет разместить.

        Сначала используется быстрый ``preview=jpeg``. Если preview-конвертация
        отсутствует, запрашивается RGB PNG; последним fallback скачивается
        исходный PNG/JPEG/WebP. Pillow для этого не требуется.
        """

        quality = max(1, min(100, int(quality)))
        base_query = {
            "filename": image_info.get("filename", ""),
            "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "output"),
        }

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
            # Fallback ниже запросит оригинальный файл.
            preview_raw = None

        if preview_raw and preview_raw[:2] == b"\xff\xd8":
            destination = destination.with_suffix(".jpg")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(preview_raw)
            return destination

        # Если preview-конвертация отсутствует, сначала просим RGB-версию.
        # Совместимые серверы обычно возвращают PNG, который поддерживается
        # даже старыми Photoshop лучше, чем WebP.
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

        if rgb_raw and rgb_raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raw, suffix = rgb_raw, ".png"
        elif rgb_raw and rgb_raw[:2] == b"\xff\xd8":
            raw, suffix = rgb_raw, ".jpg"
        else:
            raw = self._request(
                "GET",
                "/view?" + urllib.parse.urlencode(base_query),
                timeout=120,
            )
            if raw.startswith(b"\x89PNG\r\n\x1a\n"):
                suffix = ".png"
            elif raw[:2] == b"\xff\xd8":
                suffix = ".jpg"
            elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
                suffix = ".webp"
            else:
                raise UserVisibleError(
                    "ComfyUI returned an unknown image format through /view."
                )

        destination = destination.with_suffix(suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        return destination

    @staticmethod
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
    modified: float
    modified_ns: int
    # Хеш вычисляется только для выбранного workflow во время полного анализа.
    sha256: str = ""

    def public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.workflow_id,
            "name": self.name,
            "relative_path": self.relative_path,
            "size": self.size,
            "modified": self.modified,
            "modified_ns": self.modified_ns,
            "sha256": self.sha256,
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
            raise UserVisibleError("Workflow folder is not set.")
        if not self.folder.exists():
            raise UserVisibleError(f"Workflow folder does not exist: {self.folder}")
        if not self.folder.is_dir():
            raise UserVisibleError(f"Workflow path is not a folder: {self.folder}")

    def _workflow_from_path(self, path: Path, *, compute_hash: bool = False) -> WorkflowFile:
        relative = path.relative_to(self.folder).as_posix()
        stat = path.stat()
        return WorkflowFile(
            workflow_id=stable_workflow_id(relative),
            name=path.stem,
            relative_path=relative,
            absolute_path=path,
            size=stat.st_size,
            modified=stat.st_mtime,
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
                normalized = candidate.relative_to(root).as_posix()
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
        raise UserVisibleError("The selected workflow is no longer present in the folder.")

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
                f"JSON error in {workflow_file.relative_path}, line {exc.lineno}: {exc.msg}"
            ) from exc
        except OSError as exc:
            raise UserVisibleError(f"Could not read {workflow_file.absolute_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise UserVisibleError("The API workflow root must be a JSON object.")
        return data


def write_json_atomic(path: Path, data: Dict[str, Any], description: str) -> None:
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
            f"sufficient permissions.\n\n{exc}"
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

    def downstream_nodes(self, start_id: str, max_depth: int = 20) -> Set[str]:
        visited: Set[str] = set()
        frontier = [(str(start_id), 0)]
        while frontier:
            node_id, depth = frontier.pop()
            if depth >= max_depth:
                continue
            for target_id, _, _ in self.outgoing.get(node_id, []):
                if target_id not in visited:
                    visited.add(target_id)
                    frontier.append((target_id, depth + 1))
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
        self.diagnostics: List[Dict[str, str]] = []


    def validate_api_format(self) -> None:
        if "nodes" in self.workflow and "links" in self.workflow:
            raise UserVisibleError(
                "The file uses the regular ComfyUI UI format. Open it in ComfyUI "
                "and choose Workflow/File → Export (API)."
            )
        if not self.workflow:
            raise UserVisibleError("The workflow is empty.")

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
                "Invalid API nodes without class_type/inputs: " + ", ".join(invalid[:20])
            )
        if missing_classes:
            for class_type in sorted(missing_classes):
                self.error(f"The ComfyUI node is not installed: {class_type}")

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

    def info(self, message: str) -> None:
        LOGGER.info("Workflow analysis: %s", message)
        self.diagnostics.append({"level": "info", "message": message})

    def warning(self, message: str) -> None:
        LOGGER.warning("Workflow analysis: %s", message)
        self.diagnostics.append({"level": "warning", "message": message})

    def error(self, message: str) -> None:
        LOGGER.error("Workflow analysis: %s", message)
        self.diagnostics.append({"level": "error", "message": message})


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
                            meta={"reference": is_reference, "tagged": title_has_tag(title, "input"), "node_id": str(node_id), "input": input_name},
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
                                meta={"reference": is_reference, "tagged": title_has_tag(title, "input"), "node_id": str(node_id), "input": input_name},
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

    def mask_candidates(self, input_choice: Optional[Candidate]) -> List[Candidate]:
        result: List[Candidate] = []
        tagged: List[Candidate] = []

        input_nodes: List[str] = []
        if input_choice:
            for target in input_choice.targets:
                node_id = str(target.node_id)
                node = self.workflow.get(node_id, {})
                class_norm = normalize_name(node.get("class_type", "")) if isinstance(node, dict) else ""
                if "loadimage" in class_norm and "mask" not in class_norm and node_id not in input_nodes:
                    input_nodes.append(node_id)
        if input_nodes:
            connected = any(
                any(source_slot == 1 for _target_id, source_slot, _input_name in self.graph.outgoing.get(node_id, []))
                for node_id in input_nodes
            )
            result.append(
                Candidate(
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
            )

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

    def _find_upstream_text_targets(self, source_id: str, semantic_id: str = "") -> List[TargetBinding]:
        return [target for _score, target in self._find_upstream_text_candidates(source_id, semantic_id)]

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
                        + f". {matching_inputs[0]} is used as the standard control; the others remain available as advanced fields."
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
                        "The first deterministic match is used; rename or tag the intended node to make the workflow unambiguous."
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

    def analyze(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        overrides = overrides or {}
        self.validate_api_format()

        input_candidates = self.input_candidates()
        output_candidates = self.output_candidates()
        size_candidates = self.size_candidates()
        scored_sampler_candidates = self.sampler_candidates()
        sampler_candidates = [node_id for _score, node_id in scored_sampler_candidates]

        main_input_candidates = [item for item in input_candidates if not item.meta.get("reference")] or input_candidates
        input_override = str(overrides.get("input") or "")
        input_choice = self.find_candidate(main_input_candidates, input_override) if input_override else self.choose_unique_candidate(main_input_candidates)
        if input_override and not input_choice:
            self.error("The selected image input no longer exists. Open workflow settings and select it again.")
        elif not input_choice and main_input_candidates:
            self.error("Several possible image inputs were found. Select the main input in workflow settings or tag it #PS-INPUT.")

        mask_candidates = self.mask_candidates(input_choice)
        mask_override = str(overrides.get("mask") or "")
        mask_choice = self.choose_mask_candidate(mask_candidates, mask_override)
        if mask_override and not mask_choice:
            self.error("The selected inpaint mask input no longer exists. Open workflow settings and select it again.")

        reference_ids = overrides.get("references") if isinstance(overrides.get("references"), list) else []
        reference_choices: List[Candidate] = []
        found_reference_ids: Set[str] = set()
        for candidate in input_candidates:
            if input_choice and candidate.id == input_choice.id:
                continue
            if candidate.id in reference_ids or (not reference_ids and candidate.meta.get("reference")):
                reference_choices.append(candidate)
                if candidate.id in reference_ids:
                    found_reference_ids.add(candidate.id)
        missing_reference_ids = [item for item in reference_ids if item not in found_reference_ids]
        if missing_reference_ids:
            self.warning(
                "Some selected reference inputs no longer exist and were ignored: "
                + ", ".join(missing_reference_ids[:10])
            )

        output_override = str(overrides.get("output") or "")
        output_choice = self.find_candidate(output_candidates, output_override) if output_override else self.choose_unique_candidate(output_candidates)
        if output_override and not output_choice:
            self.error("The selected output node no longer exists. Open workflow settings and select it again.")
        elif not output_choice and output_candidates:
            self.error("Several possible output nodes were found. Select the result node in workflow settings or tag it #PS-OUTPUT.")

        requested_size_mode = str(overrides.get("size_mode") or "auto").lower()
        if requested_size_mode not in {"auto", "source_image", "binding"}:
            requested_size_mode = "auto"
        size_override = str(overrides.get("size") or "")
        size_choice: Optional[Candidate] = None
        if requested_size_mode == "binding":
            if not size_override:
                self.error("Size mode is set to selected width/height fields, but no size pair is selected.")
            else:
                size_choice = self.find_candidate(size_candidates, size_override)
                if not size_choice:
                    self.error("The selected width/height fields no longer exist. Open workflow settings and select them again.")
        elif requested_size_mode == "auto":
            size_choice = self.choose_unique_candidate(size_candidates)
            if not size_choice and size_candidates:
                self.warning(
                    "Several possible width/height pairs were found. Automatic mode will use the input image size. "
                    "Select a pair explicitly in workflow settings if the workflow requires fixed size fields."
                )

        # Primary sampler определяется анализатором автоматически. Все найденные
        # sampler-контролы всё равно выводятся отдельно; неоднозначность влияет
        # только на то, какой sampler считается главным и получает короткие ID.
        primary_sampler = sampler_candidates[0] if sampler_candidates else None
        if len(scored_sampler_candidates) > 1 and scored_sampler_candidates[0][0] == scored_sampler_candidates[1][0]:
            self.warning(
                "Several equivalent sampler nodes were found. The first deterministic node is treated as primary. "
                "Add #PS-MAIN to the intended sampler title to make the choice explicit."
            )

        if not input_choice and not main_input_candidates:
            self.error("No image input node was found. Add #PS-INPUT to its title.")

        if not output_choice and not output_candidates:
            self.error("No output node was found. Add #PS-OUTPUT to Save/Preview Image.")

        # В source_image и в неоднозначном auto-режиме width/height не меняются.
        # Photoshop всё равно экспортирует JPEG нужного размера; дальнейшее
        # поведение определяется самим workflow.
        effective_size_mode = "binding" if size_choice else "source_image"
        if (
            not size_choice
            and requested_size_mode == "auto"
            and not size_candidates
            and not self.input_drives_sampler_latent(input_choice, primary_sampler)
        ):
            self.warning(
                "No editable width/height pair was found. The script will send a correctly sized JPEG, "
                "but the final size depends on workflow logic."
            )

        if not primary_sampler:
            self.warning("The primary sampler was not recognized; standard parameters may be incomplete.")

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
            "size_mode": effective_size_mode,
            "size_selection_mode": requested_size_mode,
            "has_size_binding": bool(size_choice),
            "bindings": bindings,
            "controls": controls,
            "recommended_controls": [item["id"] for item in controls if item.get("recommended")],
            "available_controls": [item["id"] for item in controls],
            "candidates": {
                "input": [item.to_dict() for item in input_candidates],
                "mask": [item.to_dict() for item in mask_candidates],
                "reference": [item.to_dict() for item in input_candidates],
                "output": [item.to_dict() for item in output_candidates],
                "size": [item.to_dict() for item in size_candidates],
            },
            "diagnostics": self.diagnostics,
        }


# ============================================================================
# CACHE АНАЛИЗА И ПРИМЕНЕНИЕ ЗНАЧЕНИЙ К WORKFLOW
# Cache проверяется по размеру/mtime/hash/UUID анализатора. WorkflowPatcher заново
# валидирует тип, диапазон и target перед каждым фактическим изменением JSON.
# ============================================================================
class SchemaCache:
    def cache_path(self, workflow_id: str) -> Path:
        return WORKFLOW_CACHE_DIR / f"{workflow_id}.json"

    def _read_payload(self, workflow_file: WorkflowFile) -> Optional[Dict[str, Any]]:
        path = self.cache_path(workflow_file.workflow_id)
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
            return data
        except Exception:
            LOGGER.warning("Corrupted workflow cache %s", workflow_file.workflow_id)
            return None

    def load_fast_bundle(
        self,
        workflow_file: WorkflowFile,
    ) -> Optional[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
        """Returns cached analysis and its compact validation schema.

        A cache written by an older helper can still provide its analysis. Its
        missing validation schema is returned as None and migrated once through
        a real /object_info request.
        """

        data = self._read_payload(workflow_file)
        if not data:
            return None
        workflow_file.sha256 = str(data.get("workflow_hash") or "")
        analysis = data.get("analysis")
        if not isinstance(analysis, dict):
            return None
        validation_schema = data.get("validation_schema")
        if (
            data.get("validation_schema_version") != VALIDATION_SCHEMA_VERSION
            or not isinstance(validation_schema, dict)
        ):
            validation_schema = None
        return analysis, validation_schema

    def load_fast(self, workflow_file: WorkflowFile) -> Optional[Dict[str, Any]]:
        """Compatibility wrapper returning only cached workflow analysis."""

        bundle = self.load_fast_bundle(workflow_file)
        return bundle[0] if bundle else None

    def save(
        self,
        workflow_file: WorkflowFile,
        analysis: Dict[str, Any],
        validation_schema: Dict[str, Any],
    ) -> None:
        path = self.cache_path(workflow_file.workflow_id)
        payload = {
            "cache_version": CACHE_VERSION,
            "analyzer_uuid": ANALYZER_UUID,
            "validation_schema_version": VALIDATION_SCHEMA_VERSION,
            "relative_path": workflow_file.relative_path,
            "file_size": workflow_file.size,
            "modified": workflow_file.modified,
            "modified_ns": workflow_file.modified_ns,
            "workflow_hash": WorkflowRepository.ensure_hash(workflow_file),
            "analysis": analysis,
            "validation_schema": validation_schema,
        }
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)

    def invalidate(self, workflow_id: str) -> None:
        try:
            self.cache_path(workflow_id).unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not delete cache %s", workflow_id)


class WorkflowPatcher:
    def __init__(self, workflow: Dict[str, Any], object_info: Dict[str, Any]):
        self.workflow = copy.deepcopy(workflow)
        self.schema = ObjectInfoSchema(object_info)
        # Реальные seed, подставленные в текущую копию workflow. Они не нужны
        # основному интерфейсу, но записываются в metadata слоя для повторения.
        self.generated_seeds: Dict[str, int] = {}
        # Неприменённые необязательные параметры не должны теряться молча.
        # Список возвращается Photoshop вместе с успешным результатом.
        self.warnings: List[str] = []

    def warning(self, message: str) -> None:
        normalized = str(message or "").strip()
        if normalized and normalized not in self.warnings:
            self.warnings.append(normalized)

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
            raise UserVisibleError(f"A node disappeared from the workflow: {node_id}.")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or input_name not in inputs:
            raise UserVisibleError(f"Node {node_id} lost input {input_name}.")
        return node, input_name, inputs

    def set_target(self, target: Dict[str, str], value: Any) -> None:
        """Записывает обычное поле с приведением типа и валидацией enum."""

        node, input_name, inputs = self._resolve_target(target)
        inputs[input_name] = self.coerce_value(node, input_name, value)

    def set_target_raw(self, target: Dict[str, str], value: Any) -> None:
        """Записывает доверенное runtime-значение без проверки статического COMBO.

        Использовать только для значений, полученных непосредственно от
        ComfyUI, например результата POST /upload/image. Пользовательские
        Пользовательские sampler/scheduler/model-поля проходят set_target().
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
                        f"Invalid value {value!r} for {class_type}.{input_name}."
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
                        f"Field {class_type}.{input_name} expects an integer, received {value!r}."
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
                    f"Field {class_type}.{input_name} expects a number, received {value!r}."
                ) from exc
            if not math.isfinite(number):
                raise UserVisibleError(
                    f"Field {class_type}.{input_name} expects a finite number, received {value!r}."
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
                "separate LoadImageMask node to the workflow."
            )

        source_node_ids = {
            str(node_id)
            for node_id in mask_binding.get("node_ids", [])
            if str(node_id)
        }
        if not source_node_ids:
            raise UserVisibleError(
                "The Main LoadImage MASK binding lost its source node. Reanalyze the workflow."
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
                "The selected Main LoadImage MASK output is no longer connected. "
                "Reanalyze the workflow."
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

        # Independent reference slots are patched only when Photoshop supplied
        # a file for that slot. Otherwise the original workflow value is kept.
        uploaded_references = uploaded_references or {}
        for reference_binding in bindings.get("reference_images", []):
            binding_id = str(reference_binding.get("id") or "")
            uploaded_reference = uploaded_references.get(binding_id)
            if not uploaded_reference:
                continue
            reference_name = uploaded_reference.get("name", "")
            reference_subfolder = uploaded_reference.get("subfolder", "")
            reference_path = f"{reference_subfolder}/{reference_name}" if reference_subfolder else reference_name
            reference_path = reference_path.replace("\\", "/")
            for target in reference_binding.get("targets", []):
                self.set_target_raw(target, reference_path)
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
                    + "\nThe input image size is used instead."
                )
                apply_dimensions = False
            else:
                raise UserVisibleError(
                    "The selected workflow size fields cannot accept the requested Photoshop size:\n• "
                    + details
                    + "\n\nOpen workflow settings and choose Input image size, Automatic, or another width/height pair."
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
                        "Reanalyze the workflow."
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
                    "Reanalyze the workflow."
                )
                continue
            if self._control_is_seed(control):
                continue
            targets = control.get("targets", [])
            if not targets:
                self.warning(
                    f"Parameter {control_id} was not applied: the schema has no target inputs. "
                    "Reanalyze the workflow."
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

    def ping(self, timeout: float = 3.0) -> Dict[str, Any]:
        options = self._request("sdapi/v1/options", timeout=timeout)
        if not isinstance(options, dict):
            return {"ok": False, "forge_neo": False}
        return {
            "ok": True,
            "forge_neo": "forge_additional_modules" in options,
        }

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
            raise UserVisibleError(f"Forge schema folder does not exist: {folder}")
        return folder.resolve()
    for folder in DEFAULT_FORGE_SCHEMA_DIRS:
        if _folder_contains_forge_schema(folder):
            return folder.resolve()
    raise UserVisibleError("Forge schema folder was not found. Select it in the script settings.")


def _read_forge_schema_file(path: Path, schema_dir: Path, stack: Optional[Set[str]] = None) -> Dict[str, Any]:
    stack = set(stack or set())
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise UserVisibleError(f"Could not read Forge schema: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UserVisibleError(f"Invalid Forge schema JSON {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise UserVisibleError(f"Forge schema {path.name} must be a JSON object.")
    if data.get("kind") != FORGE_SCHEMA_KIND or str(data.get("backend")) != "forge":
        raise UserVisibleError(f"File {path.name} is not an {APP_NAME} Forge schema.")
    if int(data.get("schema_version") or 0) != FORGE_SCHEMA_VERSION:
        raise UserVisibleError(f"Unsupported schema_version in {path.name}.")
    parent_id = str(data.get("extends") or "").strip()
    if parent_id:
        if parent_id in stack:
            raise UserVisibleError(f"Circular Forge schema inheritance: {parent_id}")
        parent_path = schema_dir / f"{safe_filename(parent_id, parent_id)}.json"
        if not parent_path.is_file():
            raise UserVisibleError(f"Base Forge schema was not found: {parent_id}")
        stack.add(parent_id)
        parent = _read_forge_schema_file(parent_path, schema_dir, stack)
        data = _schema_deep_merge(parent, data)
    data.pop("abstract", None)
    data.pop("extends", None)
    return data


def list_forge_schemas(
    schema_folder: Any = "",
) -> Tuple[List[Dict[str, Any]], Path, List[Dict[str, str]]]:
    """Lists usable Forge schemas and reports files that could not be loaded.

    Files that are valid JSON but are not img2img helper Forge schemas remain
    ignored, as before. A file that declares itself as a Forge schema is fully
    validated, including inheritance, version and numeric list metadata.
    """

    schema_dir = resolve_forge_schema_dir(schema_folder)
    items: List[Dict[str, Any]] = []
    invalid_schemas: List[Dict[str, str]] = []

    def report_invalid(path: Path, message: str) -> None:
        rendered = str(message or "Unknown schema error").strip()
        invalid_schemas.append({"file": path.name, "message": rendered})
        LOGGER.warning("Skipped invalid Forge schema %s: %s", path, rendered)

    for path in sorted(schema_dir.glob("*.json"), key=lambda item: item.name.lower()):
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            report_invalid(path, f"Could not read the file: {exc}")
            continue
        except json.JSONDecodeError as exc:
            report_invalid(
                path,
                f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            )
            continue

        # The schema folder may contain unrelated JSON files. Preserve the old
        # behavior and ignore those unless they explicitly identify themselves
        # as Forge schemas for this helper.
        if not isinstance(raw, dict) or raw.get("kind") != FORGE_SCHEMA_KIND or raw.get("backend") != "forge":
            continue

        try:
            # Validate abstract base schemas too. They are not shown in the UI,
            # but a broken base would otherwise make all derived presets vanish
            # later with a less useful error.
            _read_forge_schema_file(path, schema_dir)
            if raw.get("abstract"):
                continue
            # Runtime/profile identity is the JSON filename, not the optional
            # internal id. A copied schema may intentionally keep the same id.
            schema_id = path.stem
            order = int(raw.get("order") or 1000)
        except (UserVisibleError, TypeError, ValueError) as exc:
            report_invalid(path, str(exc))
            continue

        items.append({
            "id": schema_id,
            "label": str(raw.get("label") or schema_id),
            "ui_family": str(raw.get("ui_family") or "standard"),
            "file": path.name,
            "order": order,
        })

    items.sort(key=lambda item: (item.get("order", 1000), item["label"].lower()))
    return items, schema_dir, invalid_schemas


def get_forge_schema(schema_id: str, schema_folder: Any = "") -> Dict[str, Any]:
    schema_id = str(schema_id or "").strip()
    items, schema_dir, _invalid_schemas = list_forge_schemas(schema_folder)
    for item in items:
        if item["id"] != schema_id:
            continue
        schema = _read_forge_schema_file(schema_dir / item["file"], schema_dir)
        schema["workspace_id"] = schema_id
        schema["workflow_id"] = "forge:" + schema_id
        schema["workflow_name"] = str(schema.get("label") or schema_id)
        schema["relative_path"] = item["file"]
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
    raise UserVisibleError(f"Forge UI preset was not found: {schema_id}")


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
    sources: Optional[Sequence[str]] = None, *, force: bool = False
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
            return copy.deepcopy(FORGE_CATALOG_CACHE)

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
            model_items: List[Dict[str, str]] = []
            for item in models if isinstance(models, list) else []:
                if not isinstance(item, dict):
                    continue
                title = _strip_checkpoint_hash(
                    item.get("title") or item.get("model_name") or item.get("filename")
                )
                if title:
                    model_items.append({"label": title, "value": title})
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

        return copy.deepcopy(FORGE_CATALOG_CACHE)

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
            f"ImageStitch could not decode the selected image: {path}"
        ) from exc

    # Local round-trip validation catches an incomplete/empty encoder result
    # before the request reaches Forge Neo.
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise UserVisibleError(
            f"ImageStitch could not prepare a valid PNG image: {path}"
        )
    return "data:image/png;base64," + base64.b64encode(content).decode("ascii")


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


def _forge_runtime_control_catalog(schema: Dict[str, Any]) -> Dict[str, Any]:
    controls = schema.get("controls") if isinstance(schema.get("controls"), list) else []
    sources = {
        str(control.get("source") or "")
        for control in controls
        if isinstance(control, dict)
        and str(control.get("source") or "") in FORGE_CATALOG_SOURCES
    }
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
    raise UserVisibleError(f"Invalid value {value!r} for Forge field {field_name}.")


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
            raise UserVisibleError(f"Forge field {control_id} has no available values.")
        return _forge_match_choice(value, choices, control_id)

    if control_type == "multiselect":
        if value is None:
            values: List[Any] = []
        elif isinstance(value, (list, tuple)):
            values = list(value)
        else:
            raise UserVisibleError(f"Forge field {control_id} expects a list of values.")
        choices = _forge_control_choices(control, runtime_catalog)
        if values and not choices:
            raise UserVisibleError(f"Forge field {control_id} has no available values.")
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
                f"Forge field {control_id} expects an integer, received {value!r}."
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
                f"Forge field {control_id} expects a number, received {value!r}."
            ) from exc
        if not math.isfinite(number):
            raise UserVisibleError(
                f"Forge field {control_id} expects a finite number, received {value!r}."
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
            raise UserVisibleError(f"Forge field {control_id} produced a non-finite number.")
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


# Собирает payload только из разрешённых полей схемы. Значения скрытых контролов
# берутся из default схемы, а значения видимых — из проверенного словаря JSX.
def _run_forge_generation(task: Dict[str, Any], request_id: str) -> None:
    message = task.get("message") or {}
    schema_id = str(message.get("schema_id") or message.get("workspace_id") or "")
    schema = get_forge_schema(schema_id, message.get("schema_folder"))
    input_path = Path(str(message.get("input") or ""))
    output_dir = Path(str(message.get("output") or TEMP_DIR))
    values = message.get("values") if isinstance(message.get("values"), dict) else {}
    width = int(message.get("width") or 0)
    height = int(message.get("height") or 0)
    if not input_path.is_file():
        raise UserVisibleError(f"Photoshop temporary file was not found: {input_path}")
    if width <= 0 or height <= 0:
        width, height = read_image_dimensions(input_path)
    if width <= 0 or height <= 0:
        raise UserVisibleError("Could not determine width/height for Forge Neo.")

    client = current_forge_client()
    runtime_catalog = _forge_runtime_control_catalog(schema)
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
            raise UserVisibleError(str(generation.get("require_any_error") or "Select at least one processing mode."))

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

    negative_prompt_omitted = False
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
        # JSX намеренно удаляет Negative prompt при CFG <= 1. Отсутствие этого
        # конкретного значения нельзя заменять default из схемы.
        if payload_key == "negative_prompt" and control_id not in values:
            negative_prompt_omitted = True
            continue
        # Значение из JSX есть у видимого поля; для скрытого поля применяется
        # проверенное значение по умолчанию непосредственно из JSON-схемы.
        payload[payload_key] = _forge_control_value(control, values, runtime_catalog)

    fixed_values = schema.get("fixed_values") if isinstance(schema.get("fixed_values"), dict) else {}
    for key, value in fixed_values.items():
        if key in allowed:
            payload[key] = value

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
                raise UserVisibleError("ImageStitch cannot be used with this Forge schema.")
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

    # POST Forge блокирующий, поэтому выполняем его в отдельном потоке. Worker
    # параллельно опрашивает /progress и освобождает первый progress-сегмент JSX
    # только после появления sampling_step > 0. Если ответ пришёл слишком быстро
    # и sampling не успел попасть в polling, переключаем сегмент после завершения
    # POST, но обязательно до отправки итогового изображения.
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

    threading.Thread(
        target=forge_post_worker,
        name=f"ForgeGeneration-{request_id[:8]}",
        daemon=True,
    ).start()

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
                # /progress может кратковременно не отвечать во время загрузки
                # модели. Сам POST остаётся источником истины об успехе задачи.
                LOGGER.debug("Forge progress polling failed: %s", exc)
        post_done.wait(timeout=0.05)

    if progress_stage_started:
        while not post_done.wait(timeout=0.1):
            touch_activity()
            raise_if_generation_cancelled(request_id)
    else:
        # Быстрый путь: sampling_step не успел появиться. При ошибке первый
        # progress-сегмент получает error напрямую; при успехе всё равно делаем
        # handshake init/ACK, чтобы второй сегмент и счётчик секунд появились.
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
        "backend": "forge",
        "workspace_id": schema_id,
        "values": values,
        "generated_seeds": generated_seeds,
        "warnings": [],
    }, request_id=request_id)


@dataclass
# ============================================================================
# СОСТОЯНИЕ ПРОЦЕССА И ФОНОВЫЕ WORKERS
# GenerationState защищён lock: одновременно выполняется только одна генерация,
# а interrupt/cancel доступны из отдельного socket-командного потока.
# ============================================================================
class RuntimeConfig:
    backend_host: str = DEFAULT_COMFY_HOST
    comfy_host: str = DEFAULT_COMFY_HOST
    comfy_port: int = 8188
    forge_port: int = 7860
    comfy_input_folder: Optional[Path] = None
    comfy_output_folder: Optional[Path] = None
    workflows_folder: Path = Path.home() / "Documents" / "Comfy Workflows"
    generation_timeout: int = 20 * 60


@dataclass
class GenerationState:
    request_id: Optional[str] = None
    backend: str = "comfy"
    prompt_id: Optional[str] = None
    input_folder: Optional[Path] = None
    output_folder: Optional[Path] = None
    uploaded_images: List[Dict[str, Any]] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    # JSX закрывает listener первой стадии и после ответа "init" открывает
    # listener финальной стадии. ACK подтверждает, что переключение началось.
    # Без этого очень быстрый/cached workflow теоретически мог бы вернуть
    # результат между двумя listener-окнами.
    ack_event: threading.Event = field(default_factory=threading.Event)
    active: bool = False
    queued: bool = False


RUNTIME = RuntimeConfig()
GENERATION = GenerationState()
LAST_ACTIVITY = time.monotonic()
LAST_ACTIVITY_LOCK = threading.Lock()

# Статус backend кешируется только на время жизни этого Python-процесса.
# Первый handshake проверяет обе оболочки. Последующие handshake пингуют
# только ранее доступные оболочки. Если все они перестали отвечать, выполняется
# один аварийный ping другой оболочки: это покрывает переключение Comfy ↔ Forge.
# Полная повторная проверка также доступна вручную из настроек.
BACKEND_STATUS_LOCK = threading.Lock()
BACKEND_STATUS_CACHE: Optional[Dict[str, Any]] = None
BACKEND_STATUS_ENDPOINTS: Optional[Tuple[str, int, int]] = None
# Поиск локальных Comfy input/output-папок на Windows использует netstat/PowerShell и
# заметно дороже обычного HTTP ping. Повторяем его только один раз для endpoint
# за время жизни Python-процесса; сам /system_stats по-прежнему проверяется.
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
    GENERATION.prompt_id = None
    GENERATION.backend = backend
    GENERATION.input_folder = None
    GENERATION.output_folder = None
    GENERATION.uploaded_images = []
    GENERATION.cancel_event.clear()
    GENERATION.ack_event.clear()
    GENERATION.active = True
    GENERATION.queued = False
    try:
        raise_if_generation_cancelled(request_id)
        yield request_id
    finally:
        try:
            cleanup_comfy_request_outputs(GENERATION.output_folder, request_id)
            cleanup_uploaded_images(GENERATION.input_folder, GENERATION.uploaded_images)
        finally:
            with CANCELLED_REQUESTS_LOCK:
                CANCELLED_REQUESTS.discard(request_id)
            GENERATION.request_id = None
            GENERATION.prompt_id = None
            GENERATION.backend = "comfy"
            GENERATION.input_folder = None
            GENERATION.output_folder = None
            GENERATION.uploaded_images = []
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
# ограничений старого ExtendScript Socket/eval при Unicode control characters.
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
    progress = client.get_json("sdapi/v1/progress", timeout=5)
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


def error_answer(message: str, request_id: Optional[str] = None) -> None:
    send_data_to_jsx(
        {
            "protocol": API_PROTOCOL,
            "request_id": request_id,
            "type": "error",
            "message": message,
        }
    )


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


def current_client() -> ComfyClient:
    return ComfyClient(RUNTIME.comfy_host, RUNTIME.comfy_port)


def get_object_info(force: bool = False) -> Dict[str, Any]:
    server_key = f"{RUNTIME.comfy_host}:{RUNTIME.comfy_port}"
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
    if isinstance(references, list):
        result["references"] = [str(item) for item in references if item not in (None, "")]
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

    if not force and not overrides:
        cached_bundle = SCHEMA_CACHE.load_fast_bundle(workflow_file)
        if cached_bundle is not None:
            analysis, validation_schema = cached_bundle
            LOGGER.info("Workflow analysis: disk cache used")

    if analysis is None:
        object_info = get_object_info(force=force)
        workflow_data = repository.load_json(workflow_file)
        LOGGER.info("Workflow analysis: JSON contains %s nodes", len(workflow_data))
        analysis = WorkflowAnalyzer(workflow_data, object_info).analyze(overrides)
        validation_schema = build_validation_schema(workflow_data, object_info)
        if not overrides:
            SCHEMA_CACHE.save(workflow_file, analysis, validation_schema)
    elif validation_schema is None:
        # Legacy cache migration: keep its ready analysis, request /object_info
        # once, and append only the compact validation metadata.
        object_info = get_object_info(force=False)
        workflow_data = repository.load_json(workflow_file)
        validation_schema = build_validation_schema(workflow_data, object_info)
        SCHEMA_CACHE.save(workflow_file, analysis, validation_schema)
        LOGGER.info("Workflow analysis: legacy disk cache validation schema migrated")

    result = dict(analysis)
    result.update(
        {
            "workflow_id": workflow_file.workflow_id,
            "workflow_name": workflow_file.name,
            "relative_path": workflow_file.relative_path,
            "workflow_hash": WorkflowRepository.ensure_hash(workflow_file),
            "file_size": workflow_file.size,
            "modified": workflow_file.modified,
            "modified_ns": workflow_file.modified_ns,
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
        raise UserVisibleError("There are no visible workflow values to save.")

    repository = WorkflowRepository(RUNTIME.workflows_folder)
    workflow_file = repository.get(workflow_id, relative_path=relative_path)
    workflow_data = repository.load_json(workflow_file)
    object_info = get_object_info(force=False)
    normalized_overrides = normalize_binding_overrides(overrides)
    analysis = WorkflowAnalyzer(workflow_data, object_info).analyze(normalized_overrides)
    if not analysis.get("valid"):
        messages = [
            str(item.get("message") or "")
            for item in analysis.get("diagnostics", [])
            if str(item.get("level") or "").lower() == "error"
        ]
        raise UserVisibleError(
            "The workflow cannot be saved because its current bindings are invalid:"
            + ("\n• " + "\n• ".join(messages) if messages else "")
        )

    controls = analysis.get("controls") if isinstance(analysis.get("controls"), list) else []
    controls_by_id = {
        str(control.get("id") or ""): control
        for control in controls
        if isinstance(control, dict) and control.get("id")
    }
    patcher = WorkflowPatcher(workflow_data, object_info)
    updated = 0
    for raw_id, value in values.items():
        control_id = str(raw_id or "")
        control = controls_by_id.get(control_id)
        if not control:
            raise UserVisibleError(
                f"Could not save workflow field {control_id!r}: it is missing from the current analysis. "
                "Reanalyze the workflow and try again."
            )
        targets = control.get("targets") if isinstance(control.get("targets"), list) else []
        if not targets:
            raise UserVisibleError(
                f"Could not save workflow field {control_id!r}: it has no target inputs. "
                "Reanalyze the workflow and check its bindings."
            )
        for target in targets:
            patcher.set_target(target, value)
        updated += 1

    raw_destination = str(destination_path or "").strip()
    if not raw_destination:
        raise UserVisibleError("No destination file was selected for Save As. The source workflow was not changed.")
    destination = Path(raw_destination).expanduser()
    if destination.suffix.lower() != ".json":
        raise UserVisibleError(f"The workflow must be saved as a .json file:\n{destination}")
    write_json_atomic(destination, patcher.workflow, "workflow JSON")
    try:
        saved_relative = destination.resolve().relative_to(repository.folder.resolve()).as_posix()
        SCHEMA_CACHE.invalidate(stable_workflow_id(saved_relative))
    except (OSError, ValueError):
        saved_relative = ""
    if destination.resolve() == workflow_file.absolute_path.resolve():
        SCHEMA_CACHE.invalidate(workflow_file.workflow_id)
    return {
        "ok": True,
        "path": str(destination),
        "relative_path": saved_relative,
        "updated": updated,
    }


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
        raise UserVisibleError("There are no visible Forge schema values to save.")

    items, schema_dir, _invalid_schemas = list_forge_schemas(schema_folder)
    item = next((entry for entry in items if str(entry.get("id") or "") == str(schema_id or "")), None)
    if not item:
        raise UserVisibleError(f"Forge UI preset was not found: {schema_id}")
    path = schema_dir / str(item.get("file") or "")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise UserVisibleError(f"Could not read Forge schema: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UserVisibleError(f"Invalid Forge schema JSON {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise UserVisibleError(f"Forge schema {path.name} must be a JSON object.")

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
    updated = 0
    for raw_id, value in values.items():
        control_id = str(raw_id or "")
        if control_id == "image_stitch":
            capabilities = (
                effective_schema.get("capabilities")
                if isinstance(effective_schema.get("capabilities"), dict)
                else {}
            )
            if not _forge_bool(capabilities.get("image_stitch")):
                raise UserVisibleError("The selected Forge schema does not support ImageStitch.")
            raw["image_stitch_default"] = _forge_bool(value)
            updated += 1
            continue
        control = controls_by_id.get(control_id)
        if not control:
            raise UserVisibleError(
                f"Could not save Forge field {control_id!r}: it is absent from the selected schema."
            )
        effective_control = effective_by_id.get(control_id, control)
        control["value"] = _forge_coerce_control_value(
            effective_control, value, runtime_catalog
        )
        updated += 1

    raw_destination = str(destination_path or "").strip()
    if not raw_destination:
        raise UserVisibleError("No destination file was selected for Save As. The source Forge schema was not changed.")
    destination = Path(raw_destination).expanduser()
    if destination.suffix.lower() != ".json":
        raise UserVisibleError(f"The Forge schema must be saved as a .json file:\n{destination}")

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

    normalized_loras: List[Dict[str, Any]] = []
    seen_loras: set[str] = set()
    for item in raw_selected_loras:
        name = ""
        weight = 1.0
        if isinstance(item, str):
            text = item.strip().strip("<>")
            if text.lower().startswith("lora:"):
                text = text[5:]
            if ":" in text:
                candidate_name, candidate_weight = text.rsplit(":", 1)
                name = candidate_name.strip()
                try:
                    weight = float(candidate_weight)
                except (TypeError, ValueError):
                    weight = 1.0
            else:
                name = text.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("lora") or item.get("value") or item.get("label") or "").strip()
            try:
                weight = float(item.get("weight", 1.0))
            except (TypeError, ValueError):
                weight = 1.0
        if not name:
            continue
        key = name.lower()
        if key in seen_loras:
            continue
        seen_loras.add(key)
        weight = max(0.0, min(1.0, round(weight, 2)))
        normalized_loras.append({"name": name, "weight": weight})
    if normalized_loras:
        raw["loras"] = normalized_loras
    else:
        raw.pop("loras", None)

    write_json_atomic(destination, raw, "Forge schema JSON")
    return {
        "ok": True,
        "path": str(destination),
        "updated": updated,
    }


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
            f"The workflow completed, but selected output node #{node_id} is missing from history."
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
            f"Output node #{node_id} did not return an image. "
            "Select Save Image or Preview Image tagged #PS-OUTPUT."
        )
    image = images[0]
    if not isinstance(image, dict) or not image.get("filename"):
        raise UserVisibleError("The ComfyUI result metadata does not contain filename.")
    return image


def mark_request_cancelled(request_id: Optional[str]) -> str:
    normalized = str(request_id or "")
    if normalized:
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

    # Не прерываем другую генерацию, если пришла запоздалая команда от уже
    # завершённого request_id. При отсутствии ID отменяем текущую задачу.
    if GENERATION.request_id and normalized and normalized != GENERATION.request_id:
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


# ============================================================================
# COMFY GENERATION
# Загружает временные изображения, применяет bindings/values к копии workflow,
# ставит prompt в очередь и возвращает только первое подходящее output-изображение.
# ============================================================================
def _run_comfy_generation(task: Dict[str, Any], request_id: str) -> None:
    # generation_context отмечает задачу активной до анализа workflow и upload.
    # Поэтому interrupt первого progress-сегмента не теряется до prompt_id.
    message = task.get("message") or {}
    workflow_id = str(message.get("workflow_id") or "")
    input_path = Path(str(message.get("input") or ""))
    mask_path = Path(str(message.get("mask") or "")) if message.get("mask") else None
    inpaint_mode = str(message.get("inpaint_mode") or "")
    output_dir = Path(str(message.get("output") or TEMP_DIR))
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
    workflow_data = repository.load_json(workflow_file)
    raise_if_generation_cancelled(request_id)

    analysis = None
    validation_schema = None

    if not overrides:
        cached_bundle = SCHEMA_CACHE.load_fast_bundle(workflow_file)
        if cached_bundle is not None:
            analysis, validation_schema = cached_bundle
            LOGGER.info("Comfy generation: disk analysis cache used")

    if analysis is None:
        object_info = get_object_info(force=False)
        analysis = WorkflowAnalyzer(workflow_data, object_info).analyze(overrides)
        validation_schema = build_validation_schema(workflow_data, object_info)
        if not overrides:
            SCHEMA_CACHE.save(workflow_file, analysis, validation_schema)
    elif validation_schema is None:
        # Old cache format: retain the cached analysis, fetch full /object_info
        # once and rewrite the cache with the small validation subset.
        object_info = get_object_info(force=False)
        validation_schema = build_validation_schema(workflow_data, object_info)
        SCHEMA_CACHE.save(workflow_file, analysis, validation_schema)
        LOGGER.info("Comfy generation: legacy disk cache validation schema migrated")

    # WorkflowPatcher always receives real ComfyUI type metadata. On a current
    # disk cache hit this is the compact schema and requires no /object_info.
    object_info = validation_schema or {}
    raise_if_generation_cancelled(request_id)
    if not analysis.get("valid"):
        messages = [
            item["message"]
            for item in analysis.get("diagnostics", [])
            if item.get("level") == "error"
        ]
        raise UserVisibleError("The workflow is not ready to run:\n• " + "\n• ".join(messages))

    mask_binding = analysis.get("bindings", {}).get("inpaint_mask")
    if inpaint_mode:
        if not isinstance(mask_binding, dict) or not mask_binding.get("mode"):
            raise UserVisibleError(
                "No mask input is configured for this workflow. Open workflow settings "
                "and select the main LoadImage MASK or a LoadImageMask node."
            )
        if str(mask_binding.get("mode")) != inpaint_mode:
            raise UserVisibleError("The workflow mask configuration changed. Reopen the main script window.")
        if not mask_binding.get("connected"):
            if inpaint_mode == "input_alpha":
                raise UserVisibleError(
                    "The workflow does not use the main LoadImage MASK output. Connect MASK "
                    "to the inpaint branch or select a LoadImageMask node in workflow settings."
                )
            raise UserVisibleError(
                "The selected LoadImageMask node is not connected to the workflow. Connect its MASK output to the inpaint branch."
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
    GENERATION.input_folder = input_folder
    GENERATION.output_folder = output_folder
    if input_folder:
        cleanup_stale_comfy_uploads(input_folder)
    if output_folder:
        cleanup_stale_comfy_outputs(output_folder)

    upload_subfolder = UPLOAD_SUBFOLDER

    input_suffix = input_path.suffix.lower() if input_path.suffix else ".jpg"
    remote_name = safe_filename(f"input_{request_id}{input_suffix}")
    uploaded = client.upload_image(input_path, remote_name, upload_subfolder)
    GENERATION.uploaded_images.append(uploaded)
    raise_if_generation_cancelled(request_id)

    uploaded_mask: Optional[Dict[str, Any]] = None
    if inpaint_mode and mask_path is not None:
        mask_suffix = mask_path.suffix.lower() if mask_path.suffix else ".png"
        remote_mask_name = safe_filename(f"mask_{request_id}{mask_suffix}")
        uploaded_mask = client.upload_image(mask_path, remote_mask_name, upload_subfolder)
        GENERATION.uploaded_images.append(uploaded_mask)
        raise_if_generation_cancelled(request_id)

    uploaded_references: Dict[str, Dict[str, Any]] = {}
    generation_warnings: List[str] = []
    valid_reference_ids = {
        str(item.get("id"))
        for item in analysis.get("bindings", {}).get("reference_images", [])
        if isinstance(item, dict) and item.get("id")
    }
    for reference_index, reference in enumerate(reference_files):
        if not isinstance(reference, dict):
            generation_warnings.append(
                f"Reference #{reference_index + 1} was not applied: invalid file metadata."
            )
            continue
        binding_id = str(reference.get("binding_id") or "")
        reference_path = Path(str(reference.get("path") or ""))
        if not binding_id:
            generation_warnings.append(
                f"Reference #{reference_index + 1} was not applied: binding_id is missing."
            )
            continue
        if binding_id not in valid_reference_ids:
            generation_warnings.append(
                f"Reference {binding_id} was not applied: the input is missing from the current schema. "
                "Reanalyze the workflow."
            )
            continue
        if not reference_path.is_file():
            generation_warnings.append(
                f"Reference {binding_id} was not applied: file not found ({reference_path})."
            )
            continue
        suffix = reference_path.suffix or ".jpg"
        remote_reference_name = safe_filename(f"reference_{reference_index + 1}_{request_id}{suffix}")
        uploaded_reference = client.upload_image(reference_path, remote_reference_name, upload_subfolder)
        uploaded_references[binding_id] = uploaded_reference
        GENERATION.uploaded_images.append(uploaded_reference)
        raise_if_generation_cancelled(request_id)

    patcher = WorkflowPatcher(workflow_data, object_info)
    patched = patcher.apply(
        bindings=analysis["bindings"],
        controls=analysis["controls"],
        control_values=values,
        uploaded_image=uploaded,
        uploaded_mask=uploaded_mask,
        uploaded_references=uploaded_references,
        width=width,
        height=height,
        request_id=request_id,
        size_selection_mode=str(analysis.get("size_selection_mode") or "auto"),
    )
    for warning_message in patcher.warnings:
        if warning_message not in generation_warnings:
            generation_warnings.append(warning_message)
    for warning_message in generation_warnings:
        LOGGER.warning("Generation parameter was not applied: %s", warning_message)

    raise_if_generation_cancelled(request_id)
    client_id = "photoshop-" + uuid.uuid4().hex
    prompt_id = str(uuid.uuid4())

    GENERATION.prompt_id = prompt_id
    GENERATION.queued = True

    queue_result = client.queue_prompt(patched, client_id, prompt_id)
    actual_prompt_id = str(queue_result.get("prompt_id") or prompt_id)
    GENERATION.prompt_id = actual_prompt_id
    if request_is_cancelled(request_id):
        cancel_current_generation(request_id)
        raise CancelledError("Generation was cancelled.")

    # До фактического execution Photoshop остаётся в первом сегменте с текстом
    # «Инициализация модели...». /queue сообщает, когда именно наш prompt стал
    # running. Для очень быстрого cached workflow running можно не успеть
    # увидеть; тогда init/ACK выполняется после появления completed history.
    deadline = time.monotonic() + int(message.get("timeout") or RUNTIME.generation_timeout)
    history_entry: Optional[Dict[str, Any]] = None
    progress_stage_started = False

    while time.monotonic() < deadline:
        touch_activity()
        raise_if_generation_cancelled(request_id)

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
                    notify_generation_progress_ready(
                        request_id, "comfy", actual_prompt_id
                    )
                    progress_stage_started = True
            except UserVisibleError as exc:
                # История остаётся источником истины. Временная ошибка /queue
                # не должна прерывать уже поставленную генерацию.
                LOGGER.debug("Comfy queue polling failed: %s", exc)

        time.sleep(HISTORY_POLL_INTERVAL)
    else:
        try:
            client.interrupt(actual_prompt_id)
        except Exception:
            pass
        raise UserVisibleError("Timed out while waiting for ComfyUI generation.")

    if not history_entry:
        raise UserVisibleError("ComfyUI completed the task, but history was not found.")

    image_info = select_output_image(history_entry, analysis["bindings"]["output_image"])
    # /view конвертирует стандартный PNG/WebP output ComfyUI в JPEG на
    # лету. Обычно Photoshop получает небольшой .jpg; fallback возвращает PNG
    # или исходный формат, если preview-конвертация недоступна.
    destination = output_dir / f"{now_timestamp()}-{safe_filename(workflow_file.name)}.jpg"
    destination = client.download_image_for_photoshop(image_info, destination, quality=95)

    # init/ACK уже выполнен либо при переходе prompt в queue_running, либо в
    # fast-completion ветке выше. Поэтому итоговый путь всегда относится ко
    # второму progress-сегменту Photoshop.
    answer(
        {
            "path": str(destination),
            "prompt_id": actual_prompt_id,
            "workflow_id": workflow_file.workflow_id,
            "workflow_hash": WorkflowRepository.ensure_hash(workflow_file),
            "values": values,
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
            error_answer(str(exc), task.get("request_id"))
        except Exception as exc:
            log_exception("Unhandled generation error")
            error_answer(f"Internal Python error: {exc}", task.get("request_id"))
        finally:
            # Состояние backend и временные Comfy-upload очищает
            # generation_context; worker отвечает только за очередь задач.
            GENERATION_QUEUE.task_done()


def _backend_probe_result(name: str, host: str, port: int, started: float, *,
                          available: bool, details: Optional[Dict[str, Any]] = None,
                          error: str = "", checked: bool = True) -> Dict[str, Any]:
    return {
        "name": name,
        "available": bool(available),
        "host": host,
        "port": int(port),
        "latency_ms": int(round((time.monotonic() - started) * 1000)) if checked else 0,
        "details": details or {},
        "error": str(error or ""),
        "checked": bool(checked),
    }


def _compose_backend_status(comfy: Dict[str, Any], forge: Dict[str, Any]) -> Dict[str, Any]:
    available = [name for name, item in (("comfy", comfy), ("forge", forge)) if item.get("available")]
    mode = "both" if len(available) == 2 else (available[0] if available else "none")
    return {
        "mode": mode,
        "available_backends": available,
        "backends": {"comfy": comfy, "forge": forge},
    }


def _probe_comfy(host: str, port: int, *, update_runtime: bool) -> Dict[str, Any]:
    global COMFY_INPUT_FOLDER_ENDPOINT
    started = time.monotonic()
    endpoint = (normalize_comfy_host(host), int(port))
    try:
        details = ComfyClient(host, int(port)).ping(timeout=2.0)
        if update_runtime and COMFY_INPUT_FOLDER_ENDPOINT != endpoint:
            RUNTIME.comfy_input_folder = detect_comfy_input_folder(details, host, int(port))
            RUNTIME.comfy_output_folder = detect_comfy_output_folder(details, host, int(port))
            COMFY_INPUT_FOLDER_ENDPOINT = endpoint
        # Полный /system_stats нужен Python для определения input-папки, но JSX
        # использует только available/latency. Не гоняем большой JSON обратно.
        public_details = {"ok": True}
        return _backend_probe_result(
            "comfy", host, int(port), started, available=True, details=public_details,
        )
    except Exception as exc:
        if update_runtime:
            RUNTIME.comfy_input_folder = None
            RUNTIME.comfy_output_folder = None
            COMFY_INPUT_FOLDER_ENDPOINT = None
        return _backend_probe_result(
            "comfy", host, int(port), started, available=False, error=str(exc),
        )


def _probe_forge(host: str, port: int) -> Dict[str, Any]:
    global FORGE_CATALOG_CACHE_SERVER
    started = time.monotonic()
    try:
        client = ForgeClient(host, int(port), timeout=2.0)
        options = client.get_json("sdapi/v1/options", timeout=2.0)
        is_forge_neo = isinstance(options, dict) and "forge_additional_modules" in options
        details = {"ok": isinstance(options, dict), "forge_neo": bool(is_forge_neo)}
        if is_forge_neo:
            # /options уже был получен для probe. Сохраняем current в process-cache,
            # чтобы первая загрузка Forge schema не запрашивала /options второй раз.
            server_key = (normalize_comfy_host(host), int(port))
            with FORGE_CATALOG_CACHE_LOCK:
                if FORGE_CATALOG_CACHE_SERVER != server_key:
                    FORGE_CATALOG_CACHE.clear()
                    FORGE_CATALOG_CACHE_SERVER = server_key
            _update_forge_catalog_current(options)
        return _backend_probe_result(
            "forge", host, int(port), started,
            available=is_forge_neo,
            details=details,
            error="" if is_forge_neo else "The server responded, but Forge Neo was not recognized.",
        )
    except Exception as exc:
        return _backend_probe_result(
            "forge", host, int(port), started, available=False, error=str(exc),
        )


# ============================================================================
# ОБНАРУЖЕНИЕ BACKEND И HANDSHAKE
# Comfy и Forge проверяются независимо; результат содержит режим none/comfy/forge/both.
# ============================================================================
def probe_backends(host: str, comfy_port: int, forge_port: int, *,
                   update_runtime: bool = False) -> Dict[str, Any]:
    """Полностью и независимо проверяет ComfyUI и Forge Neo.

    Эта функция используется при первом handshake и кнопкой ручного обновления
    в настройках. Недоступность одного сервера является обычным состоянием.
    """

    normalized_host = normalize_comfy_host(host)
    comfy = _probe_comfy(normalized_host, int(comfy_port), update_runtime=update_runtime)
    forge = _probe_forge(normalized_host, int(forge_port))
    return _compose_backend_status(comfy, forge)


def _backend_endpoints(host: str, comfy_port: int, forge_port: int) -> Tuple[str, int, int]:
    return normalize_comfy_host(host), int(comfy_port), int(forge_port)


def _store_backend_status(status: Dict[str, Any], endpoints: Tuple[str, int, int]) -> Dict[str, Any]:
    global BACKEND_STATUS_CACHE, BACKEND_STATUS_ENDPOINTS
    snapshot = copy.deepcopy(status)
    with BACKEND_STATUS_LOCK:
        BACKEND_STATUS_CACHE = snapshot
        BACKEND_STATUS_ENDPOINTS = endpoints
    return copy.deepcopy(snapshot)


def detect_backends(*, force_full: bool = False) -> Dict[str, Any]:
    """Возвращает статус с быстрым кешированным повторным handshake.

    Если ранее была найдена хотя бы одна оболочка, обычно проверяются только
    найденные оболочки. Если все ранее доступные backend перестали отвечать,
    дополнительно проверяется ранее недоступная оболочка. Это позволяет быстро
    обработать сценарий, когда пользователь закрыл Comfy и запустил Forge либо
    наоборот. Полная проверка обеих оболочек выполняется при пустом кеше,
    изменении адресов или ручном обновлении статуса.
    """

    endpoints = _backend_endpoints(
        RUNTIME.backend_host, RUNTIME.comfy_port, RUNTIME.forge_port
    )
    with BACKEND_STATUS_LOCK:
        cached = copy.deepcopy(BACKEND_STATUS_CACHE)
        cached_endpoints = BACKEND_STATUS_ENDPOINTS

    cached_available = list(cached.get("available_backends") or []) if isinstance(cached, dict) else []
    if force_full or cached is None or cached_endpoints != endpoints or not cached_available:
        return _store_backend_status(
            probe_backends(*endpoints, update_runtime=True), endpoints
        )

    host, comfy_port, forge_port = endpoints
    previous_backends = cached.get("backends") if isinstance(cached.get("backends"), dict) else {}
    if "comfy" in cached_available:
        comfy = _probe_comfy(host, comfy_port, update_runtime=True)
    else:
        comfy = copy.deepcopy(previous_backends.get("comfy") or _backend_probe_result(
            "comfy", host, comfy_port, time.monotonic(), available=False,
            error="Not checked: use manual status refresh.", checked=False,
        ))
        comfy["checked"] = False
        comfy["latency_ms"] = 0

    if "forge" in cached_available:
        forge = _probe_forge(host, forge_port)
    else:
        forge = copy.deepcopy(previous_backends.get("forge") or _backend_probe_result(
            "forge", host, forge_port, time.monotonic(), available=False,
            error="Not checked: use manual status refresh.", checked=False,
        ))
        forge["checked"] = False
        forge["latency_ms"] = 0

    status = _compose_backend_status(comfy, forge)
    if status.get("available_backends"):
        return _store_backend_status(status, endpoints)

    # Быстрый путь не нашёл ни одной ранее доступной оболочки. Проверяем только
    # альтернативный backend, который в этом handshake ещё не опрашивался.
    # Повторно пинговать уже проверенный сервер не требуется.
    if "comfy" in cached_available and "forge" not in cached_available:
        forge = _probe_forge(host, forge_port)
    elif "forge" in cached_available and "comfy" not in cached_available:
        comfy = _probe_comfy(host, comfy_port, update_runtime=True)

    return _store_backend_status(_compose_backend_status(comfy, forge), endpoints)

def apply_handshake(message: Dict[str, Any]) -> Dict[str, Any]:
    previous_endpoints = _backend_endpoints(
        RUNTIME.backend_host, RUNTIME.comfy_port, RUNTIME.forge_port
    )
    host = message.get("host")
    if host:
        RUNTIME.backend_host = normalize_comfy_host(host)
        RUNTIME.comfy_host = RUNTIME.backend_host
    if message.get("comfyPort"):
        RUNTIME.comfy_port = int(message["comfyPort"])
    if message.get("forgePort"):
        RUNTIME.forge_port = int(message["forgePort"])
    workflows_folder = message.get("workflowsFolder")
    if workflows_folder:
        RUNTIME.workflows_folder = Path(str(workflows_folder))
    if message.get("generationTimeout"):
        RUNTIME.generation_timeout = max(30, int(message["generationTimeout"]))

    endpoints_changed = previous_endpoints != _backend_endpoints(
        RUNTIME.backend_host, RUNTIME.comfy_port, RUNTIME.forge_port
    )
    status = detect_backends(
        force_full=bool(message.get("refreshBackends")) or endpoints_changed
    )
    runtime_data = {
        "host": RUNTIME.backend_host,
        "comfy_port": RUNTIME.comfy_port,
        "forge_port": RUNTIME.forge_port,
        "comfy_input_folder": str(RUNTIME.comfy_input_folder or ""),
        "comfy_output_folder": str(RUNTIME.comfy_output_folder or ""),
        "workflows_folder": str(RUNTIME.workflows_folder),
        "generation_timeout": RUNTIME.generation_timeout,
        "updated": time.time(),
    }
    try:
        RUNTIME_FILE.write_text(json.dumps(runtime_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        LOGGER.warning("Could not write runtime.json")
    result = {
        "ok": True,
        "app_dir": str(APP_DIR),
        "log_file": str(LOG_FILE),
        "version": VERSION,
        "protocol": API_PROTOCOL,
    }
    result.update(status)
    return result


# ============================================================================
# ДИСПЕТЧЕР КОМАНД JSX И ЛОКАЛЬНЫЙ SOCKET-СЕРВЕР
# Быстрые команды отвечают сразу. Генерация помещается в очередь и сначала
# отправляет ACK init, чтобы JSX переключился со стадии подготовки на ожидание.
# ============================================================================
def handle_command(command: Dict[str, Any]) -> None:
    touch_activity()
    request_id = command.get("request_id")
    command_type = str(command.get("type") or "")
    message = command.get("message")
    command_started = time.monotonic()
    LOGGER.info("API command: type=%s request=%s", command_type, request_id)
    if not isinstance(message, dict):
        message = {} if message in (None, "") else {"value": message}

    try:
        protocol = command.get("protocol")
        if protocol is not None and str(protocol) != str(API_PROTOCOL):
            raise UserVisibleError(
                f"Incompatible API protocol version: {protocol}; expected {API_PROTOCOL}."
            )

        if command_type == "ping":
            answer({"ok": True, "version": VERSION, "protocol": API_PROTOCOL}, request_id)
            return

        if command_type == "handshake":
            answer(apply_handshake(message), request_id)
            return

        if command_type == "backend_status":
            answer(detect_backends(force_full=False), request_id)
            return

        if command_type == "probe_backends":
            # Ручной поиск запущенных backend инвалидирует накопленные Forge-
            # каталоги. Следующая выбранная schema загрузит только нужные данные.
            clear_forge_catalog_cache()
            answer(probe_backends(
                str(message.get("host") or RUNTIME.backend_host),
                int(message.get("comfyPort") or RUNTIME.comfy_port),
                int(message.get("forgePort") or RUNTIME.forge_port),
                update_runtime=False,
            ), request_id)
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
            force = command_type == "workflow_reinitialize" or bool(message.get("force"))
            if force:
                SCHEMA_CACHE.invalidate(workflow_id)
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
            answer(forge_catalog(sources, force=bool(message.get("force"))), request_id)
            return

        if command_type == "translate":
            source_text = str(message.get("text") or message.get("value") or "").strip()
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
                raise UserVisibleError(f"Could not translate prompt: {exc}") from exc
            answer(str(translated or ""), request_id)
            return

        if command_type in {"generate", "forge_generate"}:
            # Проверка и постановка должны быть атомарными: handle_client работает
            # в отдельных потоках, и два почти одновременных запроса не должны
            # пройти проверку GENERATION_QUEUE.empty() одновременно.
            with GENERATION_SUBMIT_LOCK:
                if GENERATION.active or GENERATION.queued or not GENERATION_QUEUE.empty():
                    raise UserVisibleError("The previous generation has not finished yet.")
                # Резервируем единственный слот до queue.put(). Worker сначала
                # выставляет active=True и только затем снимает queued, поэтому
                # между приёмом команды и началом run_generation больше нет окна.
                GENERATION.queued = True
                GENERATION_QUEUE.put(command)
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
        error_answer(str(exc), request_id)
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
        error_answer(str(exc))
    except Exception:
        log_exception("TCP client handling error")
        error_answer("Photoshop local connection to Python failed.")
    finally:
        try:
            client_socket.close()
        except OSError:
            pass


def write_lock_file() -> None:
    LOCK_FILE.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": API_HOST,
                "port": API_RECEIVE_PORT,
                "started": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def remove_lock_file() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def idle_watcher() -> None:
    while not WORKER_STOP.wait(5.0):
        with LAST_ACTIVITY_LOCK:
            idle = time.monotonic() - LAST_ACTIVITY
        if (
            idle > IDLE_TIMEOUT_SECONDS
            and not GENERATION.active
            and not GENERATION.queued
            and GENERATION_QUEUE.empty()
        ):
            LOGGER.info("Shutting down after %.0f seconds of inactivity", idle)
            WORKER_STOP.set()
            # Пустое подключение будит accept().
            try:
                with socket.create_connection((API_HOST, API_RECEIVE_PORT), timeout=1):
                    pass
            except OSError:
                pass
            return


def load_runtime_file() -> None:
    global RUNTIME
    try:
        if not RUNTIME_FILE.exists():
            return
        data = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
        RUNTIME.backend_host = normalize_comfy_host(data.get("host"))
        RUNTIME.comfy_host = RUNTIME.backend_host
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
    except Exception:
        LOGGER.warning("Could not read runtime.json")


# Создаёт lock/runtime-файлы, запускает workers и принимает команды до idle timeout.
def start_local_server() -> None:
    # Проверяем и при необходимости устанавливаем зависимости до открытия
    # локального API. После успешного старта translate и ImageStitch больше не
    # запускают pip посреди пользовательской операции.
    prepare_required_modules()
    cleanup_old_temp_files()
    load_runtime_file()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((API_HOST, API_RECEIVE_PORT))
    except OSError as exc:
        # Lock-файл активного процесса не перезаписываем и не удаляем: второй
        # экземпляр просто завершается после неудачного bind.
        LOGGER.error("Could not bind port %s: %s", API_RECEIVE_PORT, exc)
        try:
            server.close()
        except OSError:
            pass
        return

    write_lock_file()
    atexit.register(remove_lock_file)

    worker_thread = threading.Thread(target=generation_worker, name="GenerationWorker", daemon=True)
    worker_thread.start()
    watcher_thread = threading.Thread(target=idle_watcher, name="IdleWatcher", daemon=True)
    watcher_thread.start()

    server.listen(8)
    server.settimeout(1.0)
    LOGGER.info(
        "%s %s started. API %s:%s, host %s, ComfyUI port %s, log=%s",
        APP_NAME,
        VERSION,
        API_HOST,
        API_RECEIVE_PORT,
        RUNTIME.comfy_host,
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
        cancel_current_generation()
        try:
            server.close()
        except OSError:
            pass
        remove_lock_file()
        LOGGER.info("%s stopped", APP_NAME)


if __name__ == "__main__":
    try:
        start_local_server()
    except Exception:
        log_exception("Critical startup error")
        remove_lock_file()
