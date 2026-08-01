#!/usr/bin/env python3
"""
main.py — Tiny Image Compressor

Compress all images inside a selected folder (and subfolders) to a target size.
Optionally choose an output root folder; otherwise creates '_tiny_imgs' subfolders
inside each source folder.
"""

import sys
import traceback
from pathlib import Path

from src.ui_styles.ui_style_shell import run_shell
from src.assets.tiny_img import compress_folder


def resolve_config(relative_path: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).parent
    return str(base_path / relative_path)


def compress_task(folder: str, output_root: str, target_size: int,
                  output_format: str, progress_callback=None) -> str:
    """
    Wrapper for compress_folder, compatible with the UI shell.
    """
    if not folder or not Path(folder).is_dir():
        return f"'{folder}' is not a valid folder."

    try:
        result = compress_folder(
            folder,
            target_size_kb=target_size,
            output_format=output_format,
            output_root=output_root if output_root else None,  # 空字符串转为 None
            progress_callback=progress_callback,
        )
        return result
    except Exception as exc:
        return f"Compression failed: {exc}"


if __name__ == "__main__":
    try:
        config_file = "src/configs/tiny_img_ui_config.json"
        resolved_path = resolve_config(config_file)
        sys.exit(run_shell(compress_task, resolved_path))
    except Exception as e:
        print("A fatal error occurred before the UI could launch:\n")
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)