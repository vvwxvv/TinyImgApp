# assets/tiny_img.py
import io
import sys
from pathlib import Path
from typing import Optional, Tuple, Callable, List

from PIL import Image

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------
SUPPORTED_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}
SUPPORTED_FORMATS = {'PNG', 'WEBP', 'JPEG'}


# ----------------------------------------------------------------------
# 单张图片压缩（支持格式转换）
# ----------------------------------------------------------------------
def compress_image(
    input_path: str,
    output_path: Optional[str] = None,
    target_size_kb: float = 700.0,
    output_format: Optional[str] = None,
    min_quality: int = 30,
    max_quality: int = 95,
    max_iterations: int = 12,
) -> Tuple[str, int]:
    """
    压缩单张图片到目标大小（KB），可选转换格式。
    返回 (输出路径, 最终字节数)。
    """
    if output_path is None:
        output_path = input_path

    target_bytes = int(target_size_kb * 1024)

    with Image.open(input_path) as img:
        original_format = img.format
        if original_format not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported input format: {original_format}")

        out_fmt = output_format.upper() if output_format else original_format
        if out_fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported output format: {out_fmt}")

        # 颜色模式适配
        if out_fmt == 'JPEG' and img.mode != 'RGB':
            img = img.convert('RGB')
        elif out_fmt in ('PNG', 'WEBP') and img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGBA' if img.info.get('transparency') else 'RGB')

        working_img = img.copy()

        # ---- 1. 尝试不缩放压缩 ----
        if out_fmt in ('JPEG', 'WEBP'):
            quality = _compress_lossy(
                working_img, out_fmt, target_bytes,
                min_quality, max_quality, max_iterations
            )
            with io.BytesIO() as buf:
                _save_with_quality(working_img, out_fmt, buf, quality)
                size = buf.tell()
                if size <= target_bytes:
                    _write_output(buf, output_path)
                    return output_path, size

        elif out_fmt == 'PNG':
            # 无损
            with io.BytesIO() as buf:
                working_img.save(buf, format='PNG', optimize=True, compress_level=9)
                size = buf.tell()
                if size <= target_bytes:
                    _write_output(buf, output_path)
                    return output_path, size

            # 量化颜色
            for colors in [256, 128, 64, 32]:
                quantized = _quantize_png(working_img, colors)
                with io.BytesIO() as buf:
                    quantized.save(buf, format='PNG', optimize=True, compress_level=9)
                    size = buf.tell()
                    if size <= target_bytes:
                        _write_output(buf, output_path)
                        return output_path, size

        # ---- 2. 缩放后重试 ----
        scale = 0.9
        while scale > 0.3:
            new_w = int(working_img.width * scale)
            new_h = int(working_img.height * scale)
            resized = working_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            with io.BytesIO() as buf:
                if out_fmt == 'PNG':
                    resized.save(buf, format='PNG', optimize=True, compress_level=9)
                    size = buf.tell()
                    if size <= target_bytes:
                        _write_output(buf, output_path)
                        return output_path, size
                    for colors in [256, 128]:
                        q = _quantize_png(resized, colors)
                        with io.BytesIO() as tmp:
                            q.save(tmp, format='PNG', optimize=True, compress_level=9)
                            size = tmp.tell()
                            if size <= target_bytes:
                                _write_output(tmp, output_path)
                                return output_path, size
                else:
                    q = _compress_lossy(
                        resized, out_fmt, target_bytes,
                        min_quality, max_quality, max_iterations
                    )
                    _save_with_quality(resized, out_fmt, buf, q)
                    size = buf.tell()
                    if size <= target_bytes:
                        _write_output(buf, output_path)
                        return output_path, size
            scale -= 0.1

    raise RuntimeError(f"Could not compress {input_path} to {target_size_kb} KB.")


# ----------------------------------------------------------------------
# 文件夹批处理（输出基于根目录名 + 后缀，内部结构完整保留）
# ----------------------------------------------------------------------
def compress_folder(
    folder: str,
    target_size_kb: int,
    output_format: str,                         # 'png' 或 'webp'
    output_root: Optional[str] = None,
    recursive: bool = True,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> str:
    """
    递归压缩文件夹内所有图片。

    输出规则：
      - 始终基于输入根目录的文件夹名生成输出根目录：<根目录名>_tiny_imgs_<格式>
      - 内部所有子目录结构完全保留
      - 如果指定了 output_root，则输出到 output_root 下；
        否则在原输入目录同级生成
    """
    root = Path(folder)
    if not root.is_dir():
        return f"'{folder}' is not a valid folder."

    # 收集所有图片文件
    files: List[Path] = []
    for ext in SUPPORTED_EXTS:
        if recursive:
            files.extend(root.rglob(f"*{ext}"))
            files.extend(root.rglob(f"*{ext.upper()}"))
        else:
            files.extend(root.glob(f"*{ext}"))
            files.extend(root.glob(f"*{ext.upper()}"))

    files = list(set(files))
    total = len(files)
    if total == 0:
        return "No image files found."

    out_fmt = output_format.lower()
    if out_fmt not in ('png', 'webp'):
        raise ValueError("output_format must be 'png' or 'webp'")

    output_base = Path(output_root) if output_root else root
    suffix = f"_tiny_imgs_{out_fmt}"

    # ★ 核心改动：输出根目录 = <输入根目录名> + suffix
    base_out_dir = output_base / (root.name + suffix)

    errors = []
    processed = 0

    for src_path in files:
        try:
            # 计算相对于输入根的相对路径
            rel_path = src_path.relative_to(root)
            # 保留所有父目录（包括子目录结构）
            parent_rel = rel_path.parent

            # 输出目录 = base_out_dir / parent_rel
            out_dir = base_out_dir / parent_rel
            out_filename = src_path.stem + f".{out_fmt}"
            out_path = out_dir / out_filename

            compress_image(
                str(src_path),
                output_path=str(out_path),
                target_size_kb=float(target_size_kb),
                output_format=out_fmt.upper(),
            )
            processed += 1
        except Exception as e:
            errors.append(f"{src_path.name}: {e}")

        if progress_callback:
            progress_callback(int(processed / total * 100))

    # 生成总结信息
    msg = f"Compressed {processed} file(s) to {target_size_kb} KB ({output_format.upper()})."
    if output_root:
        msg += f"\nOutput saved under: {output_base}"
    else:
        msg += f"\nOutput saved in '{base_out_dir.name}' alongside original directory."
    if errors:
        msg += f"\n{len(errors)} error(s):\n" + "\n".join(errors)
    return msg


# ----------------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------------
def _compress_lossy(img, fmt, target_bytes, min_q, max_q, max_iter):
    low, high = min_q, max_q
    best_q = low
    for _ in range(max_iter):
        if low > high:
            break
        mid = (low + high) // 2
        with io.BytesIO() as buf:
            _save_with_quality(img, fmt, buf, mid)
            size = buf.tell()
            if size <= target_bytes:
                best_q = mid
                low = mid + 1
            else:
                high = mid - 1
    return best_q


def _save_with_quality(img, fmt, buf, quality):
    if fmt == 'JPEG':
        img.save(buf, format='JPEG', quality=quality, optimize=True)
    elif fmt == 'WEBP':
        img.save(buf, format='WEBP', quality=quality, method=6)
    elif fmt == 'PNG':
        img.save(buf, format='PNG', optimize=True, compress_level=9)
    else:
        raise ValueError(f"Unsupported format: {fmt}")


def _quantize_png(img, colors):
    if img.mode == 'RGBA':
        return img.convert('RGBA').quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    else:
        return img.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)


def _write_output(buf, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(buf.getbuffer())


# ----------------------------------------------------------------------
# CLI 入口（支持单张或文件夹）
# ----------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Compress images to a target file size (KB) with optional format conversion."
    )
    parser.add_argument("input", help="Input image file or folder path.")
    parser.add_argument("-o", "--output", help="Output file path (for single image) or output root folder (for folder mode).")
    parser.add_argument("-s", "--size", type=float, default=700.0,
                        help="Target size in kilobytes (default: 700).")
    parser.add_argument("--format", choices=['png', 'webp', 'jpeg', 'auto'],
                        default='auto',
                        help="Output format. 'auto' keeps original format (default: auto).")
    parser.add_argument("--folder", action="store_true",
                        help="Treat input as a folder and compress all images inside.")
    parser.add_argument("--no-recursive", action="store_true",
                        help="If --folder, do not recurse into subdirectories.")
    parser.add_argument("--min-quality", type=int, default=30,
                        help="Minimum quality for JPEG/WebP (1-100).")
    parser.add_argument("--max-quality", type=int, default=95,
                        help="Maximum quality for JPEG/WebP (1-100).")
    args = parser.parse_args()

    try:
        out_fmt = None if args.format == 'auto' else args.format.upper()

        if args.folder:
            folder = args.input
            if not Path(folder).is_dir():
                print(f"Error: '{folder}' is not a valid folder.", file=sys.stderr)
                sys.exit(1)
            if out_fmt is None:
                print("Error: For folder mode, you must specify --format (png, webp, or jpeg).",
                      file=sys.stderr)
                sys.exit(1)
            result = compress_folder(
                folder,
                target_size_kb=int(args.size),
                output_format=out_fmt.lower(),
                output_root=args.output,
                recursive=not args.no_recursive,
                progress_callback=lambda p: print(f"\rProgress: {p}%", end='', flush=True)
            )
            print("\n" + result)
        else:
            # 单张图片
            in_path = args.input
            out_path = args.output
            if out_path is None:
                if out_fmt:
                    p = Path(in_path)
                    out_path = str(p.with_suffix(f'.{out_fmt.lower()}'))
                else:
                    out_path = in_path
            out_path, final_size = compress_image(
                in_path,
                out_path,
                target_size_kb=args.size,
                output_format=out_fmt,
                min_quality=args.min_quality,
                max_quality=args.max_quality,
            )
            print(f"Compressed to {out_path} ({final_size / 1024:.2f} KB)")

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()