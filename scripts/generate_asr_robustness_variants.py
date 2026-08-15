from __future__ import annotations

import argparse
import math
import random
import wave
from array import array
from pathlib import Path


def read_pcm(path: Path) -> tuple[array, tuple[int, int, int]]:
    with wave.open(str(path), "rb") as source:
        params = (source.getnchannels(), source.getsampwidth(), source.getframerate())
        if params != (1, 2, 16_000):
            raise ValueError(f"unsupported WAV format: {params}")
        samples = array("h")
        samples.frombytes(source.readframes(source.getnframes()))
    return samples, params


def write_pcm(path: Path, samples: array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(samples.tobytes())


def rms(samples: array) -> float:
    return math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))


def transform(samples: array, gain: float, snr_db: float | None, seed: int) -> array:
    random_source = random.Random(seed)
    signal_rms = rms(samples) * gain
    noise_rms = 0.0 if snr_db is None else signal_rms / (10 ** (snr_db / 20.0))
    values = array("h")
    for sample in samples:
        value = sample * gain
        if noise_rms:
            value += random_source.gauss(0.0, noise_rms)
        values.append(round(max(-32_768, min(32_767, value))))
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    variants = [
        ("gain-50", 0.50, None),
        ("gain-20", 0.20, None),
        ("gain-10", 0.10, None),
        ("snr-20", 0.60, 20.0),
        ("snr-10", 0.60, 10.0),
        ("snr-5", 0.60, 5.0),
    ]
    for input_value in args.input:
        source = Path(input_value)
        samples, _ = read_pcm(source)
        for index, (name, gain, snr_db) in enumerate(variants):
            write_pcm(output / f"{source.stem}-{name}.wav", transform(samples, gain, snr_db, index + 91))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
