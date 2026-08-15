# Third-Party Notices

Nexus is licensed under the Apache License 2.0. It uses third-party open-source components that remain subject to their own licenses. This file lists the principal direct dependencies used by the source tree for runtime, build, and test purposes; transitive dependency metadata is authoritative when a package manager resolves a build.

## Android application and build

| Component | Purpose | License |
| --- | --- | --- |
| AndroidX / Jetpack Compose / Material Components and icons | Android UI and lifecycle | Apache-2.0 |
| Kotlin and kotlinx.coroutines | Language and asynchronous runtime | Apache-2.0 |
| Coil | Image loading | Apache-2.0 |
| OkHttp and MockWebServer | HTTP client and tests | Apache-2.0 |
| Gson | JSON serialization | Apache-2.0 |
| sherpa-onnx | On-device streaming speech recognition runtime | Apache-2.0 |
| ONNX Runtime (bundled by sherpa-onnx) | Neural-network execution runtime | MIT |
| Apache Commons Compress | Extraction of the optional speech model archive | Apache-2.0 |
| Gradle and Android Gradle Plugin | Build tooling | Apache-2.0 |
| JUnit 4 | Unit tests only | EPL-1.0 |

## Gateway and tests

| Component | Purpose | License |
| --- | --- | --- |
| aiohttp | Gateway HTTP server/client runtime | Apache-2.0 AND MIT |
| edge-tts | Online Microsoft Edge speech synthesis client; most files are LGPLv3 and `srt_composer.py` is MIT | LGPL-3.0 and MIT |
| aiohttp-cors | Test/development compatibility | Apache-2.0 |
| pytest | Python tests | MIT |
| pytest-asyncio | Async Python tests | Apache-2.0 |
| PyYAML | Compose contract tests | MIT |
| pip-audit | Dependency vulnerability audit tooling | Apache-2.0 |
| PyInstaller | fnOS native runtime packaging | GPL-2.0-or-later with the PyInstaller Bootloader Exception |

## License texts and attribution

The Apache License 2.0 text is included in `LICENSE`. Components distributed only as development/test tools are not bundled into the Gateway ZIP or Android runtime unless their package metadata says otherwise. Copyright notices and complete license texts for resolved artifacts are available from their upstream source distributions and package metadata. The optional sherpa-onnx speech model is downloaded separately from the official sherpa-onnx `asr-models` release and is not embedded in the APK.

The fnOS runtime and published Gateway container redistribute `edge-tts`. Those forms must retain the exact installed distribution's license metadata and modifiable Python source. The fnOS verifier rejects a runtime that omits either item. Before a public release, maintainers must still inspect the produced container and both FPK files and confirm that the corresponding source and LGPL/MIT license texts are present; a failed or skipped inspection blocks that release.

### ONNX Runtime MIT license

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Hermes Agent is not copied into or redistributed by Nexus. Nexus only integrates with a separately operated original Hermes instance through its public HTTP API. Hermes Agent and Nous Research names belong to their respective owners; see `NOTICE` and `TRADEMARKS.md`.
