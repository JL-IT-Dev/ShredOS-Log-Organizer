#!/usr/bin/env python3
import os
import sys
import re
import shutil
import string
import argparse
from pathlib import Path
from datetime import datetime

# Regex para extraer fecha/hora, UUID y SN del reporte del sistema
SYSTEM_REPORT_PATTERN = re.compile(
    r"^nwipe_system_report_(?P<ts>\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})_host-UUID-(?P<uuid>[a-f0-9\-]+)_host-SN-(?P<sn>[^\.]+)\.",
    re.IGNORECASE
)

NWIPE_LOG_PATTERN = re.compile(r"^nwipe_log_(?P<log_ts>\d{8}-\d{6})\.txt", re.IGNORECASE)


def detect_removable_drives() -> list[str]:
    """Detecta letras de unidades USB (DRIVE_REMOVABLE = 2) en Windows."""
    drives = []
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        for letter in string.ascii_uppercase:
            drive_root = f"{letter}:\\"
            if kernel32.GetDriveTypeW(drive_root) == 2:
                drives.append(drive_root)
    return drives


def sanitize_filename(name: str) -> str:
    """Elimina caracteres inválidos para rutas de Windows."""
    sanitized = re.sub(r'[\\/*?:"<>|]', '_', name).strip()
    return sanitized if sanitized else "SN_Desconocido"


def parse_timestamp(ts_str: str) -> datetime:
    """Convierte '2026-09-08-22-52-08' a objeto datetime."""
    return datetime.strptime(ts_str, "%Y-%m-%d-%H-%M-%S")


def parse_log_timestamp(ts_str: str) -> datetime:
    """Convierte '20260908-183624' a objeto datetime."""
    return datetime.strptime(ts_str, "%Y%m%d-%H%M%S")


def organize_shredos_logs(source_dir: Path, dest_dir: Path, copy_only: bool = False):
    if not source_dir.exists():
        print(f"[ERROR] La ruta origen no existe: {source_dir}")
        return

    # Listar únicamente archivos sueltos en la raíz para no afectar carpetas ya organizadas
    root_files = [f for f in source_dir.iterdir() if f.is_file()]
    
    # 1. Encontrar todos los reportes de sistema
    system_reports = []
    for file in root_files:
        match = SYSTEM_REPORT_PATTERN.match(file.name)
        if match:
            system_reports.append((file, match.group("ts"), match.group("uuid"), match.group("sn")))

    if not system_reports:
        print(f"[INFO] No se encontraron certificados 'nwipe_system_report' en la raíz de {source_dir}.")
        return

    print(f"[INFO] Se encontraron {len(system_reports)} certificado(s) de sistema.")

    # 2. Procesar cada máquina detectada
    for report_file, ts_str, uuid, raw_sn in system_reports:
        report_dt = parse_timestamp(ts_str)
        sn = sanitize_filename(raw_sn)
        
        if sn in ("To_Be_Filled_By_O_E_M_", "Default_string", "None", ""):
            sn = f"SN_Desconocido_{uuid[:8]}"

        target_folder = dest_dir / sn
        target_folder.mkdir(parents=True, exist_ok=True)
        print(f"\n[+] Procesando equipo SN: {sn}")
        print(f"    Carpeta destino: {target_folder}")

        # Buscar el log crudo de nwipe más cercano (anterior a la finalización del reporte)
        candidate_logs = []
        for file in root_files:
            log_match = NWIPE_LOG_PATTERN.match(file.name)
            if log_match:
                try:
                    log_dt = parse_log_timestamp(log_match.group("log_ts"))
                    diff_seconds = (report_dt - log_dt).total_seconds()
                    if diff_seconds >= 0:
                        candidate_logs.append((file, diff_seconds))
                except ValueError:
                    continue

        best_log = min(candidate_logs, key=lambda x: x)[0] if candidate_logs else None

        # 3. Filtrar archivos vinculados a este equipo
        files_to_transfer = []
        for file in root_files:
            name = file.name
            # Mismo certificado de sistema
            if file == report_file:
                files_to_transfer.append(file)
            # Archivo con el UUID del equipo (ej. dmesg)
            elif uuid in name:
                files_to_transfer.append(file)
            # Reportes de disco individual con el mismo timestamp
            elif name.startswith(f"nwipe_report_{ts_str}"):
                files_to_transfer.append(file)
            # Log de nwipe asociado
            elif best_log and file == best_log:
                files_to_transfer.append(file)

        # 4. Mover o copiar
        action_name = "Copiado" if copy_only else "Movido"
        for file in set(files_to_transfer):
            dest_file = target_folder / file.name
            if copy_only:
                shutil.copy2(file, dest_file)
            else:
                shutil.move(str(file), str(dest_file))
                # Remover de root_files para evitar reasignación
                if file in root_files:
                    root_files.remove(file)
            print(f"    -> {action_name}: {file.name}")

    print("\n[OK] Organización completada con éxito.")


def main():
    parser = argparse.ArgumentParser(
        description="Agrupa logs y certificados de ShredOS por Número de Serie (SN)."
    )
    parser.add_argument(
        "-u", "--usb",
        type=str,
        help="Letra de la unidad USB (ej. F: o F:\\). Si no se indica, intenta autodetección."
    )
    parser.add_argument(
        "-d", "--dest",
        type=str,
        help="Ruta destino. Si se omite, se organizará dentro de la misma USB."
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copia los archivos en lugar de moverlos (por defecto los mueve)."
    )

    args = parser.parse_args()

    usb_path = args.usb
    if not usb_path:
        detected = detect_removable_drives()
        if not detected:
            print("[ERROR] No se detectó ninguna USB extraíble automáticamente.")
            print("        Indica la unidad manualmente: python organizar_shredos.py -u F:")
            sys.exit(1)
        elif len(detected) == 1:
            usb_path = detected[0]
            print(f"[INFO] USB detectada automáticamente en: {usb_path}")
        else:
            print(f"[INFO] Se detectaron múltiples unidades: {', '.join(detected)}")
            usb_path = input("Ingresa la letra de la USB (ej. F:): ").strip()

    source = Path(usb_path if usb_path.endswith(("\\", "/")) else f"{usb_path}\\")
    dest = Path(args.dest) if args.dest else source

    organize_shredos_logs(source_dir=source, dest_dir=dest, copy_only=args.copy)


if __name__ == "__main__":
    main()