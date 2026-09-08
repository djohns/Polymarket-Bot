"""Herramienta de setup para cifrar la private key de trading real (Fase 3).

Ejecutar interactivamente en la VPS (nunca pegar la private key ni la
passphrase como argumento de línea de comandos -- quedarían en el historial
de shell y en `ps`). Ver docs/deploy.md para el procedimiento completo.

Uso:
    python scripts/encrypt_private_key.py
"""
from __future__ import annotations

import getpass
import sys

sys.path.insert(0, "src")

from polybot.execution.key_management import encrypt_private_key_to_file  # noqa: E402


def main() -> None:
    raw_key = getpass.getpass("Private key (no se muestra en pantalla): ").strip()
    passphrase = getpass.getpass("Passphrase de cifrado (no se muestra en pantalla): ").strip()
    passphrase_confirm = getpass.getpass("Repetir passphrase: ").strip()
    out_path = input("Ruta de salida [data/private_key.enc]: ").strip() or "data/private_key.enc"

    if passphrase != passphrase_confirm:
        print("Las passphrases no coinciden. Abortado.", file=sys.stderr)
        sys.exit(1)
    if not raw_key or not passphrase:
        print("Private key y passphrase no pueden estar vacías. Abortado.", file=sys.stderr)
        sys.exit(1)

    encrypt_private_key_to_file(raw_key, out_path, passphrase)
    print(f"Private key cifrada y escrita en {out_path} (permisos 600).")
    print(
        "La passphrase NO quedó guardada en ningún lado -- gestionarla vos mismo "
        "y exportarla como variable de entorno antes de arrancar el servicio "
        "(ver docs/deploy.md)."
    )


if __name__ == "__main__":
    main()
