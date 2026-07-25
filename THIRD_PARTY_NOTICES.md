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
| Gradle and Android Gradle Plugin | Build tooling | Apache-2.0 |
| JUnit 4 | Unit tests only | EPL-1.0 |

## Gateway and tests

| Component | Purpose | License |
| --- | --- | --- |
| aiohttp | Gateway HTTP server/client runtime | Apache-2.0 |
| aiohttp-cors | Test/development compatibility | Apache-2.0 |
| pytest | Python tests | MIT |
| pytest-asyncio | Async Python tests | Apache-2.0 |
| PyYAML | Compose contract tests | MIT |

## License texts and attribution

The Apache License 2.0 text is included in `LICENSE`. Components distributed only as development/test tools are not bundled into the Gateway ZIP or Android runtime unless their package metadata says otherwise. Copyright notices and complete license texts for resolved artifacts are available from their upstream source distributions and package metadata.

Hermes Agent is not copied into or redistributed by Nexus. Nexus only integrates with a separately operated original Hermes instance through its public HTTP API. Hermes Agent and Nous Research names belong to their respective owners; see `NOTICE` and `TRADEMARKS.md`.
