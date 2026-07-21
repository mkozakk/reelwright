from pathlib import Path

LUT_DIR = Path(__file__).resolve().parents[1] / "renderer" / "presets" / "luts"
LUT_SIZE = 9


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def cinematic(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    shadow_teal = (1.0 - luma) * 0.08
    highlight_orange = luma * 0.06
    return (
        clamp01(r + highlight_orange),
        clamp01(g + highlight_orange * 0.3),
        clamp01(b + shadow_teal),
    )


def vivid(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    factor = 1.3
    return (
        clamp01(luma + (r - luma) * factor),
        clamp01(luma + (g - luma) * factor),
        clamp01(luma + (b - luma) * factor),
    )


def bw(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = clamp01(0.2126 * r + 0.7152 * g + 0.0722 * b)
    return luma, luma, luma


PRESETS = {"cinematic": cinematic, "vivid": vivid, "bw": bw}


def generate(name: str, transform) -> None:
    lines = [
        f'TITLE "{name}"',
        f"LUT_3D_SIZE {LUT_SIZE}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    step = 1.0 / (LUT_SIZE - 1)
    for b_i in range(LUT_SIZE):
        for g_i in range(LUT_SIZE):
            for r_i in range(LUT_SIZE):
                r, g, b = transform(r_i * step, g_i * step, b_i * step)
                lines.append(f"{r:.6f} {g:.6f} {b:.6f}")

    LUT_DIR.mkdir(parents=True, exist_ok=True)
    (LUT_DIR / f"{name}.cube").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    for name, transform in PRESETS.items():
        generate(name, transform)
        print(f"wrote {LUT_DIR / f'{name}.cube'}")


if __name__ == "__main__":
    main()
