# Security Policy

`cerase-calc-mcp` is the calculator connector of the Cerase platform. It reads
arithmetic written by a language model, which may carry text an attacker placed
in a document or a mail the model read, so a defect in its parser could let
that text run as code or stall the server.

It is maintained by Guidance Studio S.r.l.

## Supported versions

The release unit is the `main` branch, published as
`ghcr.io/cerase-ai/cerase-calc-mcp:latest`. A fix ships as a new commit on `main`
and a new image; no earlier line is patched.

## Reporting a vulnerability

Email **tech@guidance.studio** with `cerase-calc-mcp` in the subject. That
mailbox reaches the maintainers.

Do not open an issue, a discussion or a pull request for a suspected
vulnerability. This repository is public, so any of those is a disclosure.

Include as much of this as you have:

- what the defect is, and which file or tool it is in
- the commit you tested
- the input that triggers it, or a proof of concept
- what an attacker gains, and what access they need to begin

Reports in English or Italian are equally welcome.

## Disclosure

Please hold details until a fixed image is published. We will tell you when that
happens, and we will credit you unless you prefer otherwise.

There is no paid bug bounty.
