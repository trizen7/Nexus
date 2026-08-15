from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class Sample:
    name: str
    path: Path
    reference: str


@dataclass(frozen=True)
class Result:
    engine: str
    sample: str
    reference: str
    hypothesis: str
    normalized_reference: str
    normalized_hypothesis: str
    errors: int
    reference_characters: int
    cer: float
    first_character_correct: bool
    punctuation_reference: str
    punctuation_hypothesis: str
    punctuation_accuracy: float
    duration_seconds: float
    inference_seconds: float
    real_time_factor: float


def normalize_chinese(text: str) -> str:
    return "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text)).lower()


def normalize_punctuation(text: str) -> str:
    translation = str.maketrans({",": "，", ".": "。", "?": "？", ":": "：", "!": "！"})
    normalized = text.translate(translation)
    return "".join(character for character in normalized if character in "，。？！：；")


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_character != right_character),
            ))
        previous = current
    return previous[-1]


def read_manifest(path: Path) -> list[Sample]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    samples = []
    for item in payload["samples"]:
        audio = (root / item["audio"]).resolve()
        if not audio.is_file():
            raise FileNotFoundError(audio)
        samples.append(Sample(item["name"], audio, item["reference"]))
    return samples


def sample_for_audio(path: Path) -> Sample:
    return Sample(name=path.stem, path=path.resolve(), reference="")


def read_wav(path: Path) -> tuple[list[float], float]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != SAMPLE_RATE:
            raise ValueError(f"{path} must be mono 16-bit {SAMPLE_RATE} Hz PCM")
        frames = source.readframes(source.getnframes())
        duration = source.getnframes() / SAMPLE_RATE
    samples = [int.from_bytes(frames[index:index + 2], "little", signed=True) / 32768.0
               for index in range(0, len(frames), 2)]
    return samples, duration


def make_sherpa_offline(args: argparse.Namespace) -> Callable[[Path], str]:
    import sherpa_onnx

    if args.kind == "sense_voice":
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=args.model, tokens=args.tokens, num_threads=args.threads,
            use_itn=True, language="zh",
        )
    elif args.kind == "paraformer":
        recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=args.model, tokens=args.tokens, num_threads=args.threads,
        )
    else:
        raise ValueError(f"unsupported offline kind: {args.kind}")

    def transcribe(path: Path) -> str:
        samples, _ = read_wav(path)
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        recognizer.decode_stream(stream)
        return stream.result.text.strip()

    return transcribe


def make_sherpa_online(args: argparse.Namespace) -> Callable[[Path], str]:
    import sherpa_onnx

    if args.kind == "ctc":
        recognizer = sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(
            model=args.model, tokens=args.tokens, num_threads=args.threads,
            sample_rate=SAMPLE_RATE, feature_dim=80,
        )
    elif args.kind == "transducer":
        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=args.tokens, encoder=args.encoder, decoder=args.decoder,
            joiner=args.joiner, num_threads=args.threads,
            sample_rate=SAMPLE_RATE, feature_dim=80,
        )
    else:
        raise ValueError(f"unsupported online kind: {args.kind}")

    def transcribe(path: Path) -> str:
        samples, _ = read_wav(path)
        stream = recognizer.create_stream()
        frame_samples = SAMPLE_RATE // 10
        for offset in range(0, len(samples), frame_samples):
            stream.accept_waveform(SAMPLE_RATE, samples[offset:offset + frame_samples])
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
        # Streaming encoders need right-context frames before input_finished can
        # flush the final tokens. Phone capture naturally supplies this silence.
        stream.accept_waveform(SAMPLE_RATE, [0.0] * SAMPLE_RATE)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        return recognizer.get_result(stream).strip()

    return transcribe


def make_whisper(args: argparse.Namespace) -> Callable[[Path], str]:
    executable = Path(args.executable).resolve()
    model = Path(args.model).resolve()

    def transcribe(path: Path) -> str:
        output_base = path.parent / f".{path.stem}-{args.name}"
        command = [
            str(executable), "--model", str(model), "--file", str(path),
            "--language", "zh", "--threads", str(args.threads),
            "--output-json", "--output-file", str(output_base), "--no-gpu",
        ]
        process = subprocess.run(command, capture_output=True, timeout=args.timeout, check=False)
        if process.returncode != 0:
            raise RuntimeError("whisper.cpp inference failed")
        payload = json.loads(output_base.with_suffix(".json").read_text(encoding="utf-8"))
        output_base.with_suffix(".json").unlink(missing_ok=True)
        return "".join(item.get("text", "") for item in payload.get("transcription", [])).strip()

    return transcribe


def evaluate(engine: str, sample: Sample, transcribe: Callable[[Path], str]) -> Result:
    _, duration = read_wav(sample.path)
    started = time.perf_counter()
    hypothesis = transcribe(sample.path)
    elapsed = time.perf_counter() - started
    reference = normalize_chinese(sample.reference)
    normalized = normalize_chinese(hypothesis)
    errors = edit_distance(reference, normalized)
    punctuation_reference = normalize_punctuation(sample.reference)
    punctuation_hypothesis = normalize_punctuation(hypothesis)
    punctuation_errors = edit_distance(punctuation_reference, punctuation_hypothesis)
    punctuation_denominator = max(1, len(punctuation_reference), len(punctuation_hypothesis))
    return Result(
        engine=engine,
        sample=sample.name,
        reference=sample.reference,
        hypothesis=hypothesis,
        normalized_reference=reference,
        normalized_hypothesis=normalized,
        errors=errors,
        reference_characters=len(reference),
        cer=errors / len(reference) if reference else 0.0,
        first_character_correct=bool(reference and normalized and reference[0] == normalized[0]),
        punctuation_reference=punctuation_reference,
        punctuation_hypothesis=punctuation_hypothesis,
        punctuation_accuracy=max(0.0, 1.0 - punctuation_errors / punctuation_denominator),
        duration_seconds=duration,
        inference_seconds=elapsed,
        real_time_factor=elapsed / max(duration, 0.001),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeatable Mandarin ASR benchmarks")
    parser.add_argument("--manifest")
    parser.add_argument("--audio", action="append", default=[])
    parser.add_argument("--name", required=True)
    parser.add_argument("--engine", choices=("sherpa-offline", "sherpa-online", "whisper"), required=True)
    parser.add_argument("--kind", choices=("sense_voice", "paraformer", "ctc", "transducer"))
    parser.add_argument("--model")
    parser.add_argument("--tokens")
    parser.add_argument("--encoder")
    parser.add_argument("--decoder")
    parser.add_argument("--joiner")
    parser.add_argument("--executable")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.engine == "sherpa-offline":
        transcribe = make_sherpa_offline(args)
    elif args.engine == "sherpa-online":
        transcribe = make_sherpa_online(args)
    else:
        transcribe = make_whisper(args)

    if not args.manifest and not args.audio:
        parser.error("one of --manifest or --audio is required")
    samples = read_manifest(Path(args.manifest)) if args.manifest else []
    samples.extend(sample_for_audio(Path(item)) for item in args.audio)
    results = [evaluate(args.name, sample, transcribe) for sample in samples]
    reference_characters = sum(item.reference_characters for item in results)
    summary = {
        "engine": args.name,
        "samples": len(results),
        "cer": sum(item.errors for item in results) / reference_characters if reference_characters else None,
        "first_character_accuracy": sum(item.first_character_correct for item in results) / max(1, len(results)),
        "mean_punctuation_accuracy": sum(item.punctuation_accuracy for item in results) / max(1, len(results)),
        "mean_real_time_factor": sum(item.real_time_factor for item in results) / max(1, len(results)),
        "results": [asdict(item) for item in results],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
