# Security Policy

## Supported versions

Only the latest tagged release receives security fixes. If you are on an
older version, please upgrade first and check whether the issue persists.

| Version | Supported |
|---|---|
| latest `vX.Y.Z` release | ✅ |
| older releases | ❌ |

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.**

Instead, use **GitHub's private vulnerability reporting** on the
[Security tab](https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher/security)
of this repository ("Report a vulnerability"). You will get a confirmation,
and we aim to respond within 7 days.

Please include:

- a description of the vulnerability and its potential impact,
- steps to reproduce (commands, minimal files — never attach private photos),
- the version/commit, OS, and Python version you tested with.

## Scope notes

This tool shells out to ExifTool and otherwise runs fully locally: it sends
no data anywhere (the only downloads are PyTorch weights on first ResNet use
and packages from PyPI). The most security-relevant surface is therefore:

- command construction for ExifTool subprocess calls (always argument lists,
  never a shell — please keep it that way),
- parsing of untrusted inputs (folder names, filenames, EXIF blobs, CSV
  reports) — contributions touching these paths should add regression tests.
