# cerase-calc-mcp

An MCP server that does exact decimal arithmetic, so that the figures an
assistant writes in a document or a mail are computed rather than estimated. It
evaluates arithmetic expressions, adds up and multiplies lists of numbers, and
rounds, using Python's `decimal` with 60 significant digits and never binary
floating point: `0.1 + 0.2` is `0.3`, and an amount above ten billion keeps its
cents.

It needs no network, no file and no credential.

## Tools

| Tool | What it does | Arguments | Returns |
|---|---|---|---|
| `calculate` | Evaluates an arithmetic expression: `+ - * /` (also `×` `÷` `−`), parentheses, a sign, and powers with a whole-number exponent (`^` or `**`). | `expression` (string), optional `round_to` (integer, 0 to 20), `mode` (`half_up` or `half_even`, default `half_up`) | `{value, value_it, expression, rounded, note?}` |
| `sum` | Adds up a list of numbers. | `values` (list of 1 to 1000 strings), optional `round_to`, `mode` | `{value, value_it, expression, rounded, note?}` |
| `product` | Multiplies a list of numbers. | `values` (list of 1 to 1000 strings), optional `round_to`, `mode` | `{value, value_it, expression, rounded, note?}` |
| `round` | Rounds one number to a number of decimal places. | `value` (string), `places` (integer, 0 to 20), optional `mode` | `{value, value_it, expression, rounded, note?}` |

Every tool only reads its arguments: none has a side effect, and calling one
twice gives the same answer.

## What an answer carries

| Field | Meaning | Example |
|---|---|---|
| `value` | The result in machine form: a dot for decimals, no thousands separator, never exponent notation, with the trailing zeros the computation produced. | `"1234.50"` |
| `value_it` | The same result in Italian form: a dot between thousands, a comma for decimals. | `"1.234,50"` |
| `expression` | What the server computed, as it read it: ASCII operators, a space around `+ - * /`, the parentheses that were typed, and a parenthesis around a negative operand. | `"(1234.56 + 99.9) * 1.22"` |
| `rounded` | `true` when `value` is not the exact result. | `false` |
| `note` | Present only when `rounded` is true: which rounding happened. | `"Rounded to 2 decimal places, half up (ties away from zero)."` |

`rounded` is true in three cases, and `note` names each one that applies:

- a `round_to` or a `round` call changed the value (rounding `2.5` to two places
  gives `2.50` and is not counted);
- a division, or a power with a negative exponent, does not terminate, so its
  result is carried at 60 significant digits (`1/3`);
- a result has more than 60 significant digits and is rounded to 60.

`half_up` rounds a tie away from zero (`2.345` → `2.35`, `-2.5` → `-3`) and is the
default. `half_even` rounds a tie to the even digit (`2.345` → `2.34`, `2.5` → `2`).

## What it reads

Numbers in machine form only: the ASCII digits `0`–`9`, at most one dot, a digit
on each side of the dot. `1234.56` is accepted; `1.234,56`, `1,234.56`,
`1'234.56`, `1 234.56`, `.5` and `5.` are refused, each with the reason. In
`sum`, `product` and `round` each number is a JSON string with an optional sign
(`"-0.5"`); a JSON number is refused, because the transport has already read it
as binary floating point.

Operators bind as on paper: a power before a sign, so `-2^2` is `-4`; `*` and `/`
before `+` and `-`; powers right to left, so `2^3^2` is `512`; everything else
left to right.

The expression is read by a tokenizer and a recursive-descent parser written for
this grammar. Nothing reaches `eval`, `exec`, `compile` or the `ast` module, and
any name is refused, so no input reaches Python as code.

## What it refuses

Each refusal is a tool error whose text is the reason, e.g. *a comma is not
accepted (character 2): write decimals with a dot and no thousands separator,
e.g. 1234.56, not 1.234,56 or 1,234.56*. Every limit is checked before the work
it bounds.

| Refused | Limit |
|---|---|
| An expression longer than | 2,000 characters |
| Nesting deeper than | 50 levels of parentheses, signs and powers |
| A list longer than | 1,000 items, each at most 100 characters |
| A power whose exponent is not a whole number, or is outside | −1,000 to 1,000 |
| A number or result with more digits before the decimal point than | 100 |
| A non-zero number or result closer to zero than | 10^-99 |
| A number with more significant digits than | 60 |
| Decimal places for rounding outside | 0 to 20 |
| Division by zero, `0^0`, `0` to a negative power | always |
| Names, functions, constants (`pi`, `NaN`, `Infinity`), exponent notation (`1e5`), `%`, digits other than ASCII | always |

## Build and run locally

```sh
docker build -t cerase-calc-mcp .
docker run --rm -p 127.0.0.1:3000:3000 cerase-calc-mcp
```

`server.py` speaks MCP over stdio; the image runs it behind `mcp-proxy`, which
serves Streamable HTTP at `http://localhost:3000/mcp` and SSE at
`http://localhost:3000/sse`. `scripts/smoke.py` calls the running container as a
client does:

```sh
python3 scripts/smoke.py http://127.0.0.1:3000/mcp
```

The image's `HEALTHCHECK` runs `scripts/healthcheck.py`, an MCP client that
completes the handshake and lists the tools over `/mcp`. Its
`CERASE_HEALTHCHECK_*` variables exist to point it at a stub in tests.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest tests/ -q
python3 scripts/readme-tools-check.py
```

`tests/test_server.py` covers exact results, the Italian form, both rounding
modes, every refusal above with its reason and a time bound, and the MCP surface
the tools are called through. `scripts/readme-tools-check.py` fails when the
table under *Tools* and the tools `server.py` registers differ.

## Publishing

Every push to `main` builds the image, runs the tests and the checks above, calls
the built image with `scripts/smoke.py`, and publishes it as
`ghcr.io/cerase-ai/cerase-calc-mcp` with the tags `latest`, `main` and
`sha-<short>`. A `v*` git tag publishes `X.Y.Z` and `X.Y`.

## Security

See [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
