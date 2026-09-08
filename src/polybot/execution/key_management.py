"""Cifrado en reposo de la private key de trading real (Fase 3).

Diseño (ver docs/deploy.md para el procedimiento paso a paso que ejecuta el
usuario):

- Cifrado simétrico con Fernet (AES-128-CBC + HMAC, de la librería
  `cryptography`) -- suficientemente robusto para esta escala, sin la
  complejidad operativa de gestionar un par de claves asimétrico.
- La clave Fernet no se guarda: se deriva en cada arranque a partir de una
  passphrase (que el usuario genera y gestiona fuera del repo/VPS) vía
  PBKDF2-HMAC-SHA256 con un salt. El salt NO es secreto -- viaja junto al
  archivo cifrado en texto plano -- su único rol es evitar que la misma
  passphrase produzca siempre la misma clave derivada.
- La passphrase se lee en runtime desde una variable de entorno separada del
  `.env` principal (`POLYMARKET_KEY_PASSPHRASE` por defecto, configurable vía
  `REAL_KEY_PASSPHRASE_ENV_VAR`) -- el usuario la exporta manualmente en la
  sesión de shell que arranca el servicio, nunca queda persistida en disco.
- La private key descifrada sólo existe en memoria del proceso vivo. Nunca se
  escribe a disco en claro, nunca se loguea (ni siquiera en errores -- ver
  `load_private_key`, que nunca incluye el valor de `key` en excepciones).
"""
from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_PBKDF2_ITERATIONS = 600_000
_SALT_SIZE = 16


class KeyManagementError(Exception):
    pass


def _derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_private_key_to_file(raw_private_key: str, out_path: str, passphrase: str) -> None:
    """Cifra `raw_private_key` y escribe `out_path` como `<salt_b64>:<token>`.

    Pensado para ejecutarse una sola vez, de forma interactiva, vía
    `scripts/encrypt_private_key.py` -- no se invoca desde el proceso del bot.
    """
    salt = os.urandom(_SALT_SIZE)
    fernet = Fernet(_derive_fernet_key(passphrase, salt))
    token = fernet.encrypt(raw_private_key.encode("utf-8"))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.urlsafe_b64encode(salt) + b":" + token)
    os.chmod(out_path, 0o600)


def load_private_key(encrypted_path: str, passphrase_env_var: str) -> str:
    """Descifra la private key desde `encrypted_path` usando la passphrase leída
    de la variable de entorno `passphrase_env_var`. La clave descifrada se
    devuelve sólo para mantenerla en memoria (ej. `ClobClient(key=...)`) --
    quien la reciba no debe loguearla ni persistirla.
    """
    passphrase = os.environ.get(passphrase_env_var)
    if not passphrase:
        raise KeyManagementError(
            f"Variable de entorno '{passphrase_env_var}' no está seteada -- "
            "exportar la passphrase antes de arrancar el servicio de trading real."
        )

    with open(encrypted_path, "rb") as f:
        raw = f.read()

    try:
        salt_b64, token = raw.split(b":", 1)
        salt = base64.urlsafe_b64decode(salt_b64)
    except ValueError as exc:
        raise KeyManagementError(f"Archivo cifrado '{encrypted_path}' tiene formato inválido") from exc

    fernet = Fernet(_derive_fernet_key(passphrase, salt))
    try:
        return fernet.decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise KeyManagementError(
            "No se pudo descifrar la private key -- passphrase incorrecta o archivo corrupto."
        ) from exc
